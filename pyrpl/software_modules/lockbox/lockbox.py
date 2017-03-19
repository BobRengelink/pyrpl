from __future__ import division
from ...modules import Module, SignalLauncher
from ...attributes import SelectProperty, BoolProperty, StringProperty
from ...module_attributes import ModuleProperty, ModuleListProperty, ModuleDictProperty
from .signals import *
from ...widgets.module_widgets import LockboxWidget
from ...pyrpl_utils import get_unique_name_list_from_class_list, all_subclasses
from ...async_utils import sleep, PyrplFuture, MainThreadTimer
from .stage import Stage
from . import LockboxModule, LockboxModuleDictProperty
from collections import OrderedDict
from PyQt4 import QtCore
from ...widgets.module_widgets.lockbox_widget import LockboxSequenceWidget, LockboxStageWidget


def all_classnames():
    return OrderedDict([(subclass.__name__, subclass) for subclass in
                                 [Lockbox] + all_subclasses(Lockbox)])


class ClassnameProperty(SelectProperty):
    """
    Lots of lockbox attributes need to be updated when model is changed
    """
    def set_value(self, obj, val):
        super(ClassnameProperty, self).set_value(obj, val)
        # we must save the attribute immediately here in order to guarantee
        # that make_Lockbox works
        if obj._autosave_active:
            self.save_attribute(obj, val)
        else:
            obj._logger.debug("Autosave of classname attribute of Lockbox is "
                              "inactive. This may have severe impact "
                              "on proper functionality.")
        obj._logger.debug("Lockbox classname changed to %s", val)
        # this call results in replacing the lockbox object by a new one
        obj._classname_changed()
        return val

    def options(self, instance):
        return all_classnames().keys()


class AutoLockProperty(BoolProperty):
    """ true if autolock is enabled"""
    def set_value(self, obj, val):
        super(AutoLockProperty, self).set_value(obj=obj, val=val)
        if val:
            obj._signal_launcher.timer_autolock.start()
        else:
            obj._signal_launcher.timer_autolock.stop()


class AutoLockIntervalProperty(FloatProperty):
    """ timeout for autolock timer """
    def set_value(self, obj, val):
        super(AutoLockIntervalProperty, self).set_value(obj=obj, val=val)
        obj._signal_launcher.timer_autolock.setInterval(val*1000.0)


class StateSelectProperty(SelectProperty):
    def set_value(self, obj, val):
        super(StateSelectProperty, self).set_value(obj, val)
        # save the last time of change of state
        obj._state_change_time = time()
        obj._signal_launcher.state_changed.emit()


class SignalLauncherLockbox(SignalLauncher):
    """
    A SignalLauncher for the lockbox
    """
    output_created = QtCore.pyqtSignal(list)
    output_deleted = QtCore.pyqtSignal(list)
    output_renamed = QtCore.pyqtSignal()
    stage_created = QtCore.pyqtSignal(list)
    stage_deleted = QtCore.pyqtSignal(list)
    stage_renamed = QtCore.pyqtSignal()
    delete_widget = QtCore.pyqtSignal()
    state_changed = QtCore.pyqtSignal()
    add_input = QtCore.pyqtSignal(list)
    input_calibrated = QtCore.pyqtSignal(list)
    remove_input = QtCore.pyqtSignal(list)
    update_transfer_function = QtCore.pyqtSignal(list)
    update_lockstatus = QtCore.pyqtSignal(list)

    def __init__(self, module):
        super(SignalLauncherLockbox, self).__init__(module)
        # obsolete
        # self.timer_lock = QtCore.QTimer()
        # self.timer_lock.timeout.connect(self.module.goto_next)
        # self.timer_lock.setSingleShot(True)

        self.timer_autolock = QtCore.QTimer()
        # autolock works by periodiccally calling relock
        self.timer_autolock.timeout.connect(self.call_relock)
        self.timer_autolock.setSingleShot(True)
        self.timer_autolock.setInterval(1000.0)

        self.timer_lockstatus = QtCore.QTimer()
        self.timer_lockstatus.timeout.connect(self.call_lockstatus)
        self.timer_lockstatus.setSingleShot(True)
        self.timer_lockstatus.setInterval(1000.0)

        # start timer that checks lock status
        self.timer_lockstatus.start()

    def call_relock(self):
        self.module.relock()
        self.timer_autolock.start()

    def call_lockstatus(self):
        self.module._lockstatus()
        self.timer_lockstatus.start()

    def kill_timers(self):
        """
        kill all timers
        """
        #self.timer_lock.stop()
        self.timer_autolock.stop()
        self.timer_lockstatus.stop()

class LockFuture(PyrplFuture):
    """
    A Future that will only be fulfilled (with True or False) when the whole
    lock sequence is over.
    """
    def __init__(self, lockbox, **kwds):
        """
        The kwds are the options for the "final stage" (a modified copy of
        the last stage that is enabled once the sequence is over)
        """
        self.lockbox = lockbox
        super(LockFuture, self).__init__()
        # prepare final stage property as a modified copy of the last stage
        self.lockbox.final_stage = kwds
        # iterate through locking sequence:
        # unlock -> sequence -> final_stage
        self.lockbox.unlock()

        first_stage = self.lockbox.sequence[0]
        first_stage.enable()

        self._timer = MainThreadTimer(first_stage.duration*1000)
        self._timer.timeout.connect(self.goto_next_stage)
        self._timer.start()

    def goto_next_stage(self):
        """
        Goes to the stage immediately after the current one.
        """
        next_stage = self.lockbox.current_stage.next
        if next_stage is None: #  sequence over, set result
            self.set_result(self.lockbox.is_locked()) #  Ideally,
            # this should be asynchronous as well...
        else:
            self._timer.setInterval(next_stage.duration * 1000)
            next_stage.enable()
            self._timer.start()

    def cancel(self):
        self._timer.stop()
        super(LockFuture, self).cancel()


class Lockbox(LockboxModule):
    """
    A Module that allows to perform feedback on systems that are well described
    by a physical model.
    """
    _widget_class = LockboxWidget
    _signal_launcher = SignalLauncherLockbox
    _gui_attributes = ["classname",
                       "setpoint_unit",
                       "default_sweep_output",
                       "auto_lock",
                       "error_threshold"]
    _setup_attributes = _gui_attributes + ["auto_lock_interval"]

    classname = ClassnameProperty()

    ###################
    # unit management #
    ###################
    # setpoint_unit is mandatory to specify in which unit the setpoint is given
    setpoint_unit = SelectProperty(options=['V'], default='V')
    # output gain comes in units of '_output_unit'/V of analog redpitaya output
    _output_units = ['V', 'mV']
    # each _output_unit must come with a function that allows conversion from
    # output_unit to setpoint_unit
    def _unit1_in_unit2(self, unit1, unit2, try_prefix=True):
        """ helper function to convert unit2 to unit 1"""
        if unit1 == unit2:
            return 1.0
        try:
            return getattr(self, '_'+unit1+'_in_'+unit2)
        except AttributeError:
            try:
                return 1.0 / getattr(self, '_' + unit2 + '_in_' + unit1)
            except AttributeError:
                if not try_prefix:
                    raise
        # did not find the unit. Try scaling of unit1
        _unit_prefixes = OrderedDict([('', 1.0,),
                                      ('m', 1e-3),
                                      ('u', 1e-6),
                                      ('n', 1e-9),
                                      ('p', 1e-12),
                                      ('k', 1e3),
                                      ('M', 1e6),
                                      ('G', 1e9),
                                      ('T', 1e12)])
        for prefix2 in _unit_prefixes:
            if unit2.startswith(prefix2) and len(unit2)>len(prefix2):
                for prefix1 in _unit_prefixes:
                    if unit1.startswith(prefix1) and len(unit1)>len(prefix1):
                        try:
                            return self._unit1_in_unit2(unit1[len(prefix1):],
                                                         unit2[len(prefix2):],
                                                         try_prefix=False)\
                                   * _unit_prefixes[prefix1]\
                                   / _unit_prefixes[prefix2]
                        except AttributeError:
                            pass
        raise AttributeError("Could not find attribute %s in Lockbox class. "
                             %(unit1+'_per_'+unit2))

    def _unit_in_setpoint_unit(self, unit):
        # helper function to convert setpoint_unit into unit
        return self._unit1_in_unit2(unit, self.setpoint_unit)

    def _setpoint_unit_in_unit(self, unit):
        # helper function to convert setpoint_unit into unit
        return self._unit1_in_unit2(self.setpoint_unit, unit)

    # default_sweep_output would throw an error if the saved state corresponds
    # to a nonexisting output
    default_sweep_output = SelectProperty(options=lambda lb: lb.outputs.keys(),
                                          ignore_errors=True)

    # consider cavity locked ifin units of setpoint_unit
    error_threshold = FloatProperty(default=1.0, min=-1e10,max=1e10)

    auto_lock = AutoLockProperty()
    # try to relock every auto_lock_interval (s) is autolock is on
    auto_lock_interval = AutoLockIntervalProperty(default=1.0, min=1e-3,
                                                  max=1e10)

    # logical inputs and outputs of the lockbox are accessible as
    # lockbox.outputs.output1
    inputs = LockboxModuleDictProperty(input_from_output=InputFromOutput)
    outputs = LockboxModuleDictProperty(output1=OutputSignal,
                                        output2=OutputSignal)

    # Sequence is a list of stage modules. By default the first stage is created
    sequence = ModuleListProperty(Stage, default=[{}])
    sequence._widget_class = LockboxSequenceWidget

    # current state of the lockbox
    current_state = StateSelectProperty(options=
                                          (lambda inst:
                                            ['unlock', 'sweep', 'lock']
                                            + list(range(len(inst.sequence)))),
                                        default='unlock')

    @property
    def final_stage(self):
        """ temporary storage of the final lock stage"""
        if not hasattr(self, '_final_stage'):
            self._final_stage = Stage(self, name='final_stage')
            self.final_stage = {}
        return self._final_stage

    @final_stage.setter
    def final_stage(self, kwargs):
        setup_attributes = self.sequence[-1].setup_attributes
        setup_attributes.update(kwargs)
        setup_attributes['duration'] = 0
        self.final_stage.setup(**setup_attributes)

    @property
    def current_stage(self):
        if isinstance(self.current_state, int):
            return self.sequence[self.current_state]
        elif self.current_state == 'lock':
            return self.final_stage
        else:
            return self.current_state

    @property
    def signals(self):
        """ a dict of all logical signals of the lockbox """
        # only return those signals that are already initialized to avoid
        # recursive loops at startup
        signallist = []
        if hasattr(self, "_inputs"):
            signallist += self.inputs.items()
        if hasattr(self, "_outputs"):
            signallist += self.outputs.items()
        return OrderedDict(signallist)
        #return OrderedDict(self.inputs.items()+self.outputs.items())

    @property
    def asg(self):
        """ the asg being used for sweeps """
        if not hasattr(self, '_asg') or self._asg is None:
            self._asg = self.pyrpl.asgs.pop(self.name)
        return self._asg

    def calibrate_all(self):
        """
        Calibrates successively all inputs
        """
        for input in self.inputs:
            input.calibrate()

    def unlock(self, reset_offset=True):
        """
        Unlocks all outputs.
        """
        for output in self.outputs:
            output.unlock(reset_offset=reset_offset)
        self.current_state = 'unlock'

    def sweep(self):
        """
        Performs a sweep of one of the output. No output default kwds to avoid
        problems when use as a slot.
        """
        self.unlock()
        self.outputs[self.default_sweep_output].sweep()
        self.current_state = "sweep"

    # obsolete
    # def goto_next(self):
    #     """
    #     Goes to the stage immediately after the current one
    #     """
    #     if isinstance(self.current_stage, self.sequence.element_cls):
    #         self.goto(self.current_stage.next)
    #     else:  # self.state=='sweep' or self.state=='unlock':
    #         self.goto(self.sequence[0])
    #     if self.current_stage != self.sequence[-1]:
    #         self._signal_launcher.timer_lock.setInterval(
    #             (self.current_stage).duration * 1000)
    #         self._signal_launcher.timer_lock.start()
    #
    # def goto(self, stage):
    #     """
    #     Sets up the lockbox to the stage named stage_name
    #     """
    #     stage.enable()

    def lock(self, **kwds):
        """
        Launches the full lock sequence, stage by stage until the end.
        optional kwds are stage attributes that are set after iteration through
        the sequence, e.g. a modified setpoint.
        """
        # prepare final stage property as a modified copy of the last stage
        self.final_stage = kwds
        # iterate through locking sequence:
        # unlock -> sequence -> final_stage
        self.unlock()
        for stage in self.sequence + [self.final_stage]:
            stage.enable()
            state_change_time = self._state_change_time
            # asynchronous sleep, allows other things
            # to happend in the meantime
            sleep(stage.duration)
            if self._state_change_time != state_change_time:
                # lockbox state was changed during sleep -> Abort lock
                # Remark:
                # In the async logic, self._lock_future.cancel() should be
                # called whenever a stage.enable() is called (use an
                # unprotected function stage._enable for the lock sequence
                # itself)
                return False
        return self.is_locked(loglevel=logging.DEBUG)

    def lock_new(self, **kwds):
        """
        Same as lock_async, except the function will block until the lock
        sequence is over.
        :param **kwds: The parameters to use for the final stage
        :returns the result (True or False) of the lock sequence.
        """
        is_locked = self.lock_async(**kwds)
        return is_locked.await_result() #  no timeout since duration is
        # deterministic

    def lock_async(self, **kwds):
        """
        Launches the full lock sequence, stage by stage until the end.
        optional kwds are stage attributes that are set after iteration through
        the sequence, e.g. a modified setpoint. Returns immediately a
        LockFuture object that will represent the result of the lock sequence
        (True or False) once arrived at the end of the sequence.
        """
        self._new_lock_future(**kwds)
        return self._new_lock_future()

    def _new_lock_future(self, **kwds):
        """
        Cancels the current self._lock_future, creates and returns a new one
        with the specified kwds.
        """
        if hasattr(self, '_lock_future'):
            self._lock_future.cancel()
        self._lock_future = LockFuture(self, **kwds)
        return self._lock_future

    def relock(self):
        """ locks the cavity if it is_locked is false. Returns the value of
        is_locked """
        if self.current_stage in self.sequence:
            # lock acquisition in progress, dont interrupt
            return False
        # either in final stage or in an unlocked state
        elif self.is_locked(loglevel=logging.DEBUG):
            return True
        else:
            return self.lock(**self.final_stage._setup_attributes)

    # def _setup(self):
    #     """
    #     Sets up the lockbox
    #     """
    #     for input in self.inputs:
    #         input.setup()
    #     for output in self.outputs:
    #         output._setup()

    def is_locked(self, input=None, loglevel=logging.INFO):
        """ returns True if locked, else False. Also updates an internal
        dict that contains information about the current error signals. The
        state of lock is logged at loglevel """
        if not self.current_stage in (self.sequence+[self.final_stage]):
            # not locked to any defined sequence state
            self._logger.log(loglevel, "Cavity is not locked: lockbox state "
                                       "is %s.", self.current_stage)
            return False
        # test for output saturation
        for o in self.outputs:
            if o.is_saturated:
                self._logger.log(loglevel, "Cavity is not locked: output %s "
                                           "is saturated.", o.name)
                return False
        # input locked to
        if input is None: #input=None (default) or input=False (call by gui)
            input = self.inputs[self.current_stage.input]
        try:
            # use input-specific is_locked if it exists
            try:
                islocked = input.is_locked(loglevel=loglevel)
            except TypeError: # occurs if is_locked takes no argument loglevel
                islocked = input.is_locked()
            return islocked
        except:
            pass
        # supposed to be locked at this value
        setpoint = self.current_stage.setpoint
        # current values
        #actmean, actrms = self.pyrpl.rp.sampler.mean_stddev(input.input_channel)
        actmean, actrms = input.mean, input.rms
        # get max, min of acceptable error signals
        error_threshold = self.error_threshold
        min = input.expected_signal(setpoint-error_threshold)
        max = input.expected_signal(setpoint+error_threshold)
        startslope = input.expected_slope(setpoint - error_threshold)
        stopslope = input.expected_slope(setpoint + error_threshold)
        # no guarantee that min<max
        if max<min:
            # swap them in this case
            max, min = min, max
        # now min < max
        # if slopes have unequal signs, the signal has a max/min in the
        # interval
        if startslope*stopslope <= 0:
            if startslope > stopslope:  # maximum in between, ignore upper limit
                max = 1e100
            elif startslope < stopslope:  # minimum, ignore lower limit
                min = -1e100
        if actmean > max or actmean < min:
            self._logger.log(loglevel,
                             "Cavity is not locked at stage %s: "
                             "input %s value of %.2f +- %.2f (setpoint %.2f)"
                             "is not in error interval [%.2f, %.2f].",
                             self.current_stage.name,
                             input.name,
                             actmean,
                             actrms,
                             input.expected_signal(setpoint),
                             min,
                             max)
            return False
        # lock seems ok
        self._logger.log(loglevel,
                         "Cavity is locked at stage %s: "
                         "input %s value is %.2f +- %.2f (setpoint %.2f).",
                         self.current_stage.name,
                         input.name,
                         actmean,
                         actrms,
                         input.expected_signal(setpoint))
        return True

    def _lockstatus(self):
        """ this function is a placeholder for periodic lockstatus
        diagnostics, such as calls to is_locked, logging means and rms
        values, and plotting measured setpoints etc."""
        # ask GUI to update the lockstatus display (pass value of
        # self.is_locked() instead of None if already available)
        self._signal_launcher.update_lockstatus.emit([None])
        # optionally, insert logging functionality in derived classes here...

    @classmethod
    def _make_Lockbox(cls, parent, name):
        """ returns a new Lockbox object of the type defined by the classname
        variable in the config file"""
        # identify class name
        try:
            classname = parent.c[name]['classname']
        except KeyError:
            classname = cls.__name__
            parent.logger.debug("No config file entry for classname found. "
                                "Using class '%s'.", classname)
        parent.logger.debug("Making new Lockbox with class %s. ", classname)
        # return instance of the class
        return all_classnames()[classname](parent, name)

    def _classname_changed(self):
        # check whether a new object must be instantiated and return if not
        if self.classname == type(self).__name__:
            self._logger.debug("Lockbox classname not changed: - formerly: %s, "
                               "now: %s.",
                              type(self).__name__,
                              self.classname)
            return
        self._logger.debug("Lockbox classname changed - formerly: %s, now: %s.",
                          type(self).__name__,
                          self.classname)
        # save names such that lockbox object can be deleted
        pyrpl, name = self.pyrpl, self.name
        # launch signal for widget deletion
        self._signal_launcher.delete_widget.emit()
        # delete former lockbox (free its resources)
        self._delete_Lockbox()
        # make a new object
        new_lockbox = Lockbox._make_Lockbox(pyrpl, name)
        # update references
        setattr(pyrpl, name, new_lockbox)  # pyrpl.lockbox = new_lockbox
        pyrpl.software_modules.append(new_lockbox)
        # create new dock widget
        for w in pyrpl.widgets:
            w.reload_dock_widget(name)

    def _delete_Lockbox(self):
        """ returns a new Lockbox object of the type defined by the classname
        variable in the config file"""
        pyrpl, name = self.pyrpl, self.name
        self._signal_launcher.clear()
        for o in self.outputs:
            o._clear()
        for i in self.inputs:
            i._clear()
        setattr(pyrpl, name, None)  # pyrpl.lockbox = None
        try:
            self.parent.software_modules.remove(self)
        except ValueError:
            self._logger.warning("Could not find old Lockbox %s in the list of "
                                 "software modules. Duplicate lockbox objects "
                                 "may coexist. It is recommended to restart "
                                 "PyRPL. Existing software modules: \n%s",
                                 self.name, str(self.parent.software_modules))
        # redirect all attributes of the old lockbox to the new/future lockbox
        # object
        def getattribute_forwarder(obj, attribute):
            lockbox = getattr(pyrpl, name)
            return getattr(lockbox, attribute)
        self.__getattribute__ = getattribute_forwarder
        def setattribute_forwarder(obj, attribute, value):
            lockbox = getattr(pyrpl, name)
            return setattr(lockbox, attribute, value)
        self.__setattr__ = setattribute_forwarder
