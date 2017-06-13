"""
The Iir widget allows to dynamically select zeros and poles of the iir filter
"""
from .base_module_widget import ModuleWidget
from collections import OrderedDict
from PyQt4 import QtCore, QtGui
import pyqtgraph as pg
import numpy as np
import sys

APP = QtGui.QApplication.instance()


class MyGraphicsWindow(pg.GraphicsWindow):
    def __init__(self, title, parent):
        super(MyGraphicsWindow, self).__init__(title)
        self.parent = parent
        self.setToolTip("IIR transfer function: \n"
                        "----------------------\n"
                        "CTRL + Left click: add one more pole. \n"
                        "SHIFT + Left click: add one more zero\n"
                        "Left Click: select pole (other possibility: click on the '+j' labels below the graph)\n"
                        "Left/Right arrows: change imaginary part (frequency) of the current pole or zero\n"
                        "Up/Down arrows; change the real part (width) of the current pole or zero. \n"
                        "Poles are represented by 'X', zeros by 'O'")
        self.doubleclicked = False
        #APP.setDoubleClickInterval(300)  # default value (550) is fine
        self.mouse_clicked_timer = QtCore.QTimer()
        self.mouse_clicked_timer.setSingleShot(True)
        self.mouse_clicked_timer.setInterval(APP.doubleClickInterval())
        self.mouse_clicked_timer.timeout.connect(self.mouse_clicked)

    # see https://wiki.python.org/moin/PyQt/Distinguishing%20between%20click%20and%20double%20click
    # "The trick is to realise that Qt delivers MousePress, MouseRelease,
    # MouseDoubleClick and MouseRelease events in that order to the widget."
    def mousePressEvent(self, event):
        self.doubleclicked = False
        self.storeevent(event)
        if not self.mouse_clicked_timer.isActive():
            self.mouse_clicked_timer.start()
        return super(MyGraphicsWindow, self).mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.doubleclicked = True
        self.storeevent(event)
        if self.mouse_clicked_timer.isActive():
            self.mouse_clicked_timer.stop()
            self.mouse_clicked()
        return super(MyGraphicsWindow, self).mouseDoubleClickEvent(event)

    def storeevent(self, event):
        self.button = event.button()
        self.modifier = int(event.modifiers())
        it = self.getItem(0, 0)
        pos = it.mapToScene(event.pos()) #  + it.vb.pos()
        point = it.vb.mapSceneToView(pos)
        self.x, self.y = point.x(), point.y()
        if self.parent.xlog:
            self.x = 10 ** self.x  # takes logscale into account

    def mouse_clicked(self):
        default_damping = self.x/10.0
        if self.button == QtCore.Qt.LeftButton:
            if self.doubleclicked:
                if self.modifier == QtCore.Qt.CTRL:
                    new = -default_damping - 1.j * self.x
                    self.parent.module.complex_poles += [new]
                    self.parent.parent.attribute_widgets['complex_poles'].set_selected(-1)
                if self.modifier == QtCore.Qt.SHIFT:
                    self.parent.module.complex_zeros += [new]
                    self.parent.parent.attribute_widgets['complex_zeros'].set_selected(-1)
            else:
                new = -self.x
                if self.modifier == 0:
                    # select nearest pole/zero with a simple click
                    type = 'pole'
                    pole_or_zero = 'pole'
                    value = -1001. - 1j*34
                    self.parent.module._logger.info("Frequency: %.2e, "
                                                    "%s at %s selected.",
                                                    self.x,
                                                    pole_or_zero,
                                                    value)
                if self.modifier == QtCore.Qt.CTRL:
                    # make a new real pole
                    self.parent.module.real_poles += [new]
                    self.parent.parent.attribute_widgets['real_poles'].set_selected(-1)
                if self.modifier == QtCore.Qt.SHIFT:
                    # make a new real zero
                    self.parent.module.real_zeros += [new]
                    self.parent.parent.attribute_widgets['real_zeros'].set_selected(-1)



class IirGraphWidget(QtGui.QGroupBox):
    # whether xaxis is plotted in log-scale
    xlog = True

    def __init__(self, parent):
        # graph
        self.name = "Transfer functions"
        super(IirGraphWidget, self).__init__(parent)
        self.parent = parent
        self.module = self.parent.module
        self.layout = QtGui.QVBoxLayout(self)
        self.win = MyGraphicsWindow(title="Amplitude", parent=self)
        self.win_phase = MyGraphicsWindow(title="Phase", parent=self)
        # self.proxy = pg.SignalProxy(self.win.scene().sigMouseClicked,
        # rateLimit=60, slot=self.mouse_clicked)
        self.mag = self.win.addPlot(title="Magnitude (dB)")
        self.phase = self.win_phase.addPlot(title="Phase (deg)")
        self.phase.setXLink(self.mag)
        # self.proxy_phase = pg.SignalProxy(self.win_phase.scene().sigMouseClicked,
        # rateLimit=60, slot=self.mouse_clicked)

        # we will plot the following curves:
        # poles, zeros -> large dots and crosses
        # design (designed filter) - yellow line
        # measured filter with na - orange dots <-
        # data (measruement data) - green line
        # data x design (or data/design) - red line
        self.plots = OrderedDict()
        # make lines
        for name, style in [('data', dict(pen='g')),
                            ('filter_design', dict(pen='y')),
                            ('data_x_design', dict(pen='r'))]:
            self.plots[name] = self.mag.plot(**style)
            self.plots[name + "_phase"] = self.phase.plot(**style)
            self.plots[name].setLogMode(xMode=self.xlog, yMode=None)
            self.plots[name + '_phase'].setLogMode(xMode=self.xlog, yMode=None)

        for name, style in [('filter_measurement', dict(symbol='o',
                                                 size=10,
                                                 pen='b')),
                            ('zeros', dict(pen=pg.mkPen(None),
                                           symbol='o',
                                           size=20,
                                           brush=pg.mkBrush(255, 0, 255, 120))),
                            ('poles', dict(size=20,
                                           symbol='x',
                                           pen=pg.mkPen(None),
                                           brush=pg.mkBrush(255, 0, 255,
                                                            120))),
                            # ('actpole', dict(size=30,
                            #                symbol='x',
                            #                pen='r',
                            #                brush=pg.mkBrush(255, 0, 255,
                            #                                 120))),
                            # ('actzero', dict(size=30,
                            #                symbol='o',
                            #                pen='r',
                            #                brush=pg.mkBrush(255, 0, 255,
                            #                                 120)))
                            ]:
                item = pg.ScatterPlotItem(**style)
                self.mag.addItem(item)
                self.plots[name] = item
                item = pg.ScatterPlotItem(**style)
                self.phase.addItem(item)
                self.plots[name+'_phase'] = item
        # also set logscale for the xaxis
        # make scatter plots
        self.mag.setLogMode(x=self.xlog, y=None)
        self.phase.setLogMode(x=self.xlog, y=None)
        self.layout.addWidget(self.win)
        self.layout.addWidget(self.win_phase)
        # connect signals
        self.plots['poles'].sigClicked.connect(self.parent.select_pole)
        self.plots['poles_phase'].sigClicked.connect(self.parent.select_pole)
        self.plots['zeros'].sigClicked.connect(self.parent.select_zero)
        self.plots['zeros_phase'].sigClicked.connect(self.parent.select_zero)


class IirButtonWidget(QtGui.QGroupBox):
    BUTTONWIDTH = 100

    def __init__(self, parent):
        # buttons and standard attributes
        self.name = "General settings"
        super(IirButtonWidget, self).__init__(parent)
        self.parent = parent
        self.module = self.parent.module
        self.layout = QtGui.QVBoxLayout(self)
        #self.setLayout(self.layout)  # wasnt here before
        aws = self.parent.attribute_widgets

        for attr in ['input', 'inputfilter', 'output_direct', 'loops',
                     'gain', 'on', 'bypass', 'overflow']:
            widget = aws[attr]
            widget.setFixedWidth(self.BUTTONWIDTH)
            self.layout.addWidget(widget)

        self.setFixedWidth(self.BUTTONWIDTH+50)


class IirBottomWidget(QtGui.QGroupBox):
    BUTTONWIDTH = 300

    def __init__(self, parent):
        # widget for poles and zeros
        self.name = "Filter poles and zeros"
        super(IirBottomWidget, self).__init__(parent)
        self.parent = parent
        self.module = self.parent.module
        self.layout = QtGui.QHBoxLayout(self)
        #self.setLayout(self.layout)  # wasnt here before
        aws = self.parent.attribute_widgets
        for attr in ['complex_poles', 'complex_zeros',
                     'real_poles', 'real_zeros']:
            widget = aws[attr]
            widget.setFixedWidth(self.BUTTONWIDTH)
            self.layout.addWidget(widget)


class IirWidget(ModuleWidget):
    def init_gui(self):
        self.init_main_layout(orientation="vertical")
        #self.main_layout = QtGui.QVBoxLayout()
        #self.setLayout(self.main_layout)

        # add all attribute widgets and remove them right away
        self.init_attribute_layout()
        for widget in self.attribute_widgets.values():
            self.main_layout.removeWidget(widget)

        # divide into top and bottom layout
        self.top_layout = QtGui.QHBoxLayout()
        self.main_layout.addLayout(self.top_layout)

        # add graph widget
        self.graph_widget = IirGraphWidget(self)
        self.top_layout.addWidget(self.graph_widget)

        # buttons to the right of graph
        self.button_widget = IirButtonWidget(self)
        self.top_layout.addWidget(self.button_widget)

        # poles and zeros at the bottom of the graph
        self.bottom_widget = IirBottomWidget(self)
        self.main_layout.addWidget(self.bottom_widget)

        # set colors of labels to the one of the corresponding traces
        self.attribute_widgets['data_curve'].setStyleSheet("color: green")

        self.update_plot()

        # setup filter in its present state
        self.module.setup()

    def select_pole(self, plot_item, spots):
        index = spots[0].data()
        self.attribute_widgets['poles'].set_selected(index)

    def select_zero(self, plot_item, spots):
        index = spots[0].data()
        self.attribute_widgets['zeros'].set_selected(index)

    @property
    def frequencies(self):
        try:
            f = self.module._data_curve_object.data.index.values
        except AttributeError:
            # in case data_curve is None (no curve selected)
            return np.logspace(1, np.log10(5e6), 2000)
        else:
            # avoid zero frequency (log plot)
            f[f<=0] = sys.float_info.epsilon
            return np.asarray(f, dtype=float)

    def _magnitude(self, data):
        return 20. * np.log10(np.abs(np.asarray(data, dtype=np.complex))
                              + sys.float_info.epsilon)

    def _phase(self, data):
        return np.angle(np.asarray(data, dtype=np.complex), deg=True)

    def update_plot(self):
        # first, we compile the line plot data, then we iterate over them and
        # plot them. we then plot the scatter plots in the same manner
        tfargs = {}  # args to the call of iir.transfer_function
        frequencies = self.frequencies
        plot = OrderedDict()
        # plot underlying curve data
        try:
            plot['data'] = self.module._data_curve_object.data.values
        except AttributeError:  # no curve for plotting available
            plot['data'] = []
        if False:
            # plot designed filter
            plot['filter_design'] = self.module.transfer_function(frequencies,
                                                                  **tfargs)
            # plot product
            try:
                plot['data_x_design'] = plot['data'] / plot['filter_design']
            except ValueError:
                try:
                    plot['data_x_design'] = 1.0 / plot['filter_design']
                except:
                    plot['data_x_design'] = []
            # plot everything (all lines) up to here
        for k, v in plot.items():
            self.graph_widget.plots[k].setData(frequencies[:len(v)],
                                               self._magnitude(v))
            self.graph_widget.plots[k+'_phase'].setData(frequencies[:len(v)],
                                                    self._phase(v))
        # plot poles and zeros
        aws = self.attribute_widgets
        for end in ['poles', 'zeros']:
            mag, phase = [], []
            for start in ['complex', 'real']:
                key = start+'_'+end
                freq = getattr(self.module, key)
                if start == 'complex':
                    freq = np.imag(freq)
                freq = np.abs(freq)
                tf = self.module.transfer_function(freq, **tfargs)
                selected = aws[key].get_selected()
                brush = [pg.mkBrush(color='r')
                         if (num == selected)
                         else pg.mkBrush(color='b')
                         for num in range(aws[key].number)]
                mag += [{'pos': (fr, val), 'data': i, 'brush': br}
                 for (i, (fr, val, br))
                 in enumerate(zip(list(np.log10(freq)),
                                  list(self._magnitude(tf)),
                                  brush))]
                phase += [{'pos': (fr, val), 'data': i, 'brush': br}
                 for (i, (fr, val, br))
                 in enumerate(zip(list(np.log10(freq)),
                                  list(self._phase(tf)),
                                  brush))]
            self.graph_widget.plots[end].setPoints(mag)
            self.graph_widget.plots[end+'_phase'].setPoints(phase)

