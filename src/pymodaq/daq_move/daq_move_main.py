from pymodaq.daq_utils import config
from qtpy import QtGui, QtWidgets
from qtpy.QtCore import QObject, Slot, QThread, Signal, QLocale, Qt
import sys
from pymodaq.daq_move.daq_move_gui import Ui_Form

from pymodaq.daq_move.utility_classes import params as daq_move_params
from pymodaq.daq_move.utility_classes import MoveCommand
from pathlib import Path

from pyqtgraph.parametertree import Parameter, ParameterTree
from pymodaq.daq_utils.parameter import ioxml
from pymodaq.daq_utils.parameter import utils as putils

# must be imported to register proper custom parameter types
from pymodaq.daq_utils.parameter import pymodaq_ptypes

from pymodaq.daq_utils.daq_utils import ThreadCommand
from easydict import EasyDict as edict
from pymodaq.daq_utils.tcp_server_client import TCPClient
from pymodaq.daq_utils import daq_utils as utils
from pymodaq.daq_utils.exceptions import ActuatorError
from pymodaq.daq_utils import config
local_path = config.get_set_local_dir()
sys.path.append(local_path)

logger = utils.set_logger(utils.get_module_name(__file__))

DAQ_Move_Stage_type = utils.get_plugins('daq_move')



class DAQ_Move(Ui_Form, QObject):
    """
        | DAQ_Move object is a module used to control one motor from a specified list.
        |
        | Preset is an optional list of dicts used to managers programatically settings such as the name of the controller from the list of possible controllers, COM address...
        |
        | Init is a boolean to tell the programm to initialize the controller at the start of the programm given the managers options

        ========================= =================================================
        **Attributes**             **Type**
        *command_stage*            instance of Signal
        *move_done_signal*         instance of Signal
        *update_settings_signal*   instance of Signal
        *status_signal*               instance of Signal
        *bounds_signal*            instance of Signal
        *params*                   dictionnary list
        *ui*                       instance of UI_Form
        *parent*                   QObject
        *title*                    string
        *wait_time*                int
        *initialized_state*        boolean
        *Move_done*                boolean
        *controller*               instance of the specific controller object
        *stage*                    instance of the stage (axis or wathever) object
        *current_position*         float
        *target_position*          float
        *wait_position_flag*       boolean
        *stage_types*              string list
        ========================= =================================================

        See Also
        --------
        set_enabled_move_buttons, set_setting_tree, stage_changed, quit_fun, ini_stage_fun, move_Abs, move_Rel, move_Home, get_position, stop_Motion, show_settings, show_fine_tuning

        References
        ----------
        QLocale, QObject, Signal, QStatusBar, ParameterTree
    """
    init_signal = Signal(bool)
    command_stage = Signal(ThreadCommand)
    command_tcpip = Signal(ThreadCommand)
    move_done_signal = Signal(str,
                                  float)  # to be used in external program to make sure the move has been done, export the current position. str refer to the unique title given to the module
    update_settings_signal = Signal(edict)
    status_signal = Signal(str)
    bounds_signal = Signal(bool)
    params = daq_move_params

    def __init__(self, parent, title="pymodaq Move", init=False):
        """DAQ_Move object is a module used to control one motor from a specified list.
        managers is an optional list of dicts used to managers programatically settings such as the name of the
        controller from the list of possible controllers, COM address...
        init is a boolean to tell the programm to initialize the controller at the start of the programm given the
        managers options
        To differenciate various instance of this class
        """
        self.logger = utils.set_logger(f'{logger.name}.{title}')
        self.logger.info(f'Initializing DAQ_Move: {title}')
        
        super().__init__()

        here = Path(__file__).parent
        splash = QtGui.QPixmap(str(here.parent.joinpath('splash.png')))
        self.splash_sc = QtWidgets.QSplashScreen(splash, Qt.WindowStaysOnTopHint)

        self.ui = Ui_Form()
        self.ui.setupUi(parent)
        self.ui.Moveto_pb_bis_2.setVisible(False)
        self.parent = parent
        self.ui.title_label.setText(title)
        self.title = title
        self.ui.statusbar = QtWidgets.QStatusBar(parent)
        self.ui.StatusBarLayout.addWidget(self.ui.statusbar)
        self.ui.statusbar.setMaximumHeight(20)

        self.send_to_tcpip = False
        self.tcpclient_thread = None

        self.wait_time = 1000
        self.ui.Ini_state_LED

        self.ui.Ini_state_LED.clickable = False
        self.ui.Ini_state_LED.set_as_false()
        self.ui.Move_Done_LED.clickable = False
        self.ui.Move_Done_LED.set_as_false()
        self.initialized_state = False
        self.ui.Current_position_sb.setReadOnly(False)
        self.move_done_bool = True

        # ###########IMPORTANT############################
        self.controller = None  # the hardware controller/set after initialization and to be used by other modules
        # ################################################

        self.current_position = 0
        self.target_position = 0
        self.wait_position_flag = True

        self.ui.Current_position_sb.setValue(self.current_position)
        self.set_enabled_move_buttons(enable=False)
        self.ui.groupBox.hide()
        self.parent.resize(150, 200)

        # #Setting stages types
        self.stage_types = [mov['name'] for mov in DAQ_Move_Stage_type]
        self.ui.Stage_type_combo.clear()
        self.ui.Stage_type_combo.addItems(self.stage_types)

        # create main parameter tree
        self.ui.settings_tree = ParameterTree()
        self.ui.verticalLayout_2.addWidget(self.ui.settings_tree)
        self.ui.settings_tree.setMinimumWidth(300)

        self.settings = Parameter.create(name='Settings', type='group', children=self.params)
        self.ui.settings_tree.setParameters(self.settings, showTop=False)

        # connecting from tree
        self.settings.sigTreeStateChanged.connect(
            self.parameter_tree_changed)  # any changes on the settings will update accordingly the detector
        self.ui.settings_tree.setVisible(False)
        self.set_setting_tree()

        QtWidgets.QApplication.processEvents()
        # #Connecting buttons:
        self.ui.Stage_type_combo.currentIndexChanged.connect(self.set_setting_tree)
        self.ui.Stage_type_combo.currentIndexChanged.connect(self.stage_changed)

        self.ui.Quit_pb.clicked.connect(self.quit_fun)
        self.ui.IniStage_pb.clicked.connect(self.ini_stage_fun)

        self.update_status("Ready", wait_time=self.wait_time)
        self.ui.Move_Abs_pb.clicked.connect(lambda: self.move_Abs(self.ui.Abs_position_sb.value()))
        self.ui.Move_Rel_plus_pb.clicked.connect(lambda: self.move_Rel(self.ui.Rel_position_sb.value()))
        self.ui.Move_Rel_minus_pb.clicked.connect(lambda: self.move_Rel(-self.ui.Rel_position_sb.value()))
        self.ui.Find_Home_pb.clicked.connect(self.move_Home)
        self.ui.Get_position_pb.clicked.connect(self.get_position)
        self.ui.Stop_pb.clicked.connect(self.stop_Motion)

        self.ui.parameters_pb.clicked.connect(self.show_settings)
        self.ui.fine_tuning_pb.clicked.connect(self.show_fine_tuning)
        self.ui.Abs_position_sb.valueChanged.connect(self.ui.Abs_position_sb_bis.setValue)
        self.ui.Abs_position_sb_bis.valueChanged.connect(self.ui.Abs_position_sb.setValue)
        self.ui.Moveto_pb_bis.clicked.connect(lambda: self.move_Abs(self.ui.Abs_position_sb_bis.value()))

        # initialize the controller if init=True
        if init:
            self.ui.IniStage_pb.click()

    @property
    def actuator(self):
        return self.ui.Stage_type_combo.currentText()

    @actuator.setter
    def actuator(self, actuator):
        self.ui.Stage_type_combo.setCurrentText(actuator)
        if self.actuator != actuator:
            raise ActuatorError(f'{actuator} is not a valid installed actuator: {self.stage_types}')

    def init(self):
        self.ui.IniStage_pb.click()

    def ini_stage_fun(self):
        """
            Init :
                * a DAQ_move_stage instance if not exists
                * a linked thread connected by signal to the DAQ_move_main instance

            See Also
            --------
            set_enabled_move_buttons, DAQ_utils.ThreadCommand, DAQ_Move_stage, DAQ_Move_stage.queue_command, thread_status, DAQ_Move_stage.update_settings, update_status
        """
        try:
            if not self.ui.IniStage_pb.isChecked():
                try:
                    self.set_enabled_move_buttons(enable=False)
                    self.ui.Stage_type_combo.setEnabled(True)
                    self.ui.Ini_state_LED.set_as_false()
                    self.command_stage.emit(ThreadCommand(command="close"))
                except Exception as e:
                    self.logger.exception(str(e))

            else:
                self.stage_name = self.ui.Stage_type_combo.currentText()
                stage = DAQ_Move_stage(self.stage_name, self.current_position, self.title)
                self.stage_thread = QThread()
                stage.moveToThread(self.stage_thread)

                self.command_stage[ThreadCommand].connect(stage.queue_command)
                stage.status_sig[ThreadCommand].connect(self.thread_status)
                self.update_settings_signal[edict].connect(stage.update_settings)

                self.stage_thread.stage = stage
                self.stage_thread.start()

                self.ui.Stage_type_combo.setEnabled(False)
                self.command_stage.emit(ThreadCommand(command="ini_stage",
                                                      attributes=[self.settings.child(('move_settings')).saveState(),
                                                                  self.controller]))

        except Exception as e:
            self.logger.exception(str(e))
            self.set_enabled_move_buttons(enable=False)

    def get_position(self):
        """
            Get the current position from the launched thread via the "check_position" Thread Command.

            See Also
            --------
            update_status, DAQ_utils.ThreadCommand
        """
        try:
            self.command_stage.emit(ThreadCommand(command="check_position"))

        except Exception as e:
            self.logger.exception(str(e))

    def move(self, move_command: MoveCommand):
        """Public method to trigger the correct action on the actuator. Should be used by external applications"""
        if move_command.move_type == 'abs':
            self.move_Abs(move_command.value)
        elif move_command.move_type == 'rel':
            self.move_Rel(move_command.value)
        elif move_command.move_type == 'home':
            self.move_Home(move_command.value)

    def move_Abs(self, position, send_to_tcpip=False):
        """
            | Make the move from an absolute position.
            |
            | The move is made if target is in bounds, sending the thread command "Reset_Stop_Motion" and "move_Abs".

            =============== ========== ===========================================
            **Parameters**   **Type**    **Description**

            *position*        float      The absolute target position of the move
            =============== ========== ===========================================

            See Also
            --------
            update_status, check_out_bounds, DAQ_utils.ThreadCommand
        """
        try:
            self.send_to_tcpip = send_to_tcpip
            if not (position == self.current_position and self.stage_name == "Thorlabs_Flipper"):
                self.ui.Move_Done_LED.set_as_false()
                self.move_done_bool = False
                self.target_position = position
                self.update_status("Moving", wait_time=self.wait_time)
                # self.check_out_bounds(position)
                self.command_stage.emit(ThreadCommand(command="Reset_Stop_Motion"))
                self.command_stage.emit(ThreadCommand(command="move_Abs", attributes=[position]))

        except Exception as e:
            self.logger.exception(str(e))

    def move_Home(self, send_to_tcpip=False):
        """
            Send the thread commands "Reset_Stop_Motion" and "move_Home" and update the status.

            See Also
            --------
            update_status, DAQ_utils.ThreadCommand
        """
        self.send_to_tcpip = send_to_tcpip
        try:
            self.ui.Move_Done_LED.set_as_false()
            self.move_done_bool = False
            self.update_status("Moving", wait_time=self.wait_time)
            self.command_stage.emit(ThreadCommand(command="Reset_Stop_Motion"))
            self.command_stage.emit(ThreadCommand(command="move_Home"))

        except Exception as e:
            self.logger.exception(str(e))

    def move_Rel_p(self):
        self.ui.Move_Rel_plus_pb.click()

    def move_Rel_m(self, send_to_tcpip=False):
        self.ui.Move_Rel_minus_pb.click()

    def move_Rel(self, rel_position, send_to_tcpip=False):
        """
            | Make a move from the given relative psition and the current one.
            |
            | The move is done if (current position + relative position) is in bounds sending Threads Commands "Reset_Stop_Motion" and "move_done"

            =============== ========== ===================================================
            **Parameters**   **Type**    **Description**

            *position*        float     The relative target position from the current one
            =============== ========== ===================================================

            See Also
            --------
            update_status, check_out_bounds, DAQ_utils.ThreadCommand
        """
        try:
            self.send_to_tcpip = send_to_tcpip
            self.ui.Move_Done_LED.set_as_false()
            self.move_done_bool = False
            self.target_position = self.current_position + rel_position
            self.update_status("Moving", wait_time=self.wait_time)
            # self.check_out_bounds(self.target_position)
            self.command_stage.emit(ThreadCommand(command="Reset_Stop_Motion"))
            self.command_stage.emit(ThreadCommand(command="move_Rel", attributes=[rel_position]))

        except Exception as e:
            self.logger.exception(str(e))

    def parameter_tree_changed(self, param, changes):
        """
            | Check eventual changes in the changes list parameter.
            |
            | In case of changed values, emit the signal containing the current path and parameter via update_settings_signal to the connected hardware.

            =============== ====================================    ==================================================
            **Parameters**   **Type**                                **Description**

             *param*         instance of pyqtgraph parameter         The parameter to be checked

             *changes*       (parameter,change,infos) tuple list     The (parameter,change,infos) list to be treated
            =============== ====================================    ==================================================
        """

        for param, change, data in changes:
            path = self.settings.childPath(param)
            if path is not None:
                childName = '.'.join(path)
            else:
                childName = param.name()
            if change == 'childAdded':
                if 'main_settings' not in path:
                    self.update_settings_signal.emit(edict(path=path, param=data[0].saveState(), change=change))

            elif change == 'value':

                if param.name() == 'connect_server':
                    if param.value():
                        self.connect_tcp_ip()
                    else:
                        self.command_tcpip.emit(ThreadCommand('quit'))

                elif param.name() == 'ip_address' or param.name == 'port':
                    self.command_tcpip.emit(ThreadCommand('update_connection',
                                                          dict(ipaddress=self.settings.child('main_settings', 'tcpip',
                                                                                             'ip_address').value(),
                                                               port=self.settings.child('main_settings', 'tcpip',
                                                                                        'port').value())))

                if path is not None:
                    if 'main_settings' not in path:
                        self.update_settings_signal.emit(edict(path=path, param=param, change=change))
                        if self.settings.child('main_settings', 'tcpip', 'tcp_connected').value():
                            self.command_tcpip.emit(ThreadCommand('send_info', dict(path=path, param=param)))

            elif change == 'parent':
                if param.name() not in putils.iter_children(self.settings.child('main_settings'), []):
                    self.update_settings_signal.emit(edict(path=['move_settings'], param=param, change=change))

    def connect_tcp_ip(self):
        if self.settings.child('main_settings', 'tcpip', 'connect_server').value():
            self.tcpclient_thread = QThread()

            tcpclient = TCPClient(self.settings.child('main_settings', 'tcpip', 'ip_address').value(),
                                  self.settings.child('main_settings', 'tcpip', 'port').value(),
                                  self.settings.child(('move_settings')), client_type="ACTUATOR")
            tcpclient.moveToThread(self.tcpclient_thread)
            self.tcpclient_thread.tcpclient = tcpclient
            tcpclient.cmd_signal.connect(self.process_tcpip_cmds)

            self.command_tcpip[ThreadCommand].connect(tcpclient.queue_command)

            self.tcpclient_thread.start()
            tcpclient.init_connection()

    @Slot(ThreadCommand)
    def process_tcpip_cmds(self, status):
        if 'move_abs' in status.command:
            self.move_Abs(status.attributes[0], send_to_tcpip=True)

        elif 'move_rel' in status.command:
            self.move_Rel(status.attributes[0], send_to_tcpip=True)

        elif 'move_home' in status.command:
            self.move_Home(send_to_tcpip=True)

        elif 'check_position' in status.command:
            self.send_to_tcpip = True
            self.command_stage.emit(ThreadCommand('check_position'))

        elif status.command == 'connected':
            self.settings.child('main_settings', 'tcpip', 'tcp_connected').setValue(True)

        elif status.command == 'disconnected':
            self.settings.child('main_settings', 'tcpip', 'tcp_connected').setValue(False)

        elif status.command == 'Update_Status':
            self.thread_status(status)

        elif status.command == 'set_info':
            param_dict = ioxml.XML_string_to_parameter(status.attributes[1])[0]
            param_tmp = Parameter.create(**param_dict)
            param = self.settings.child('move_settings', *status.attributes[0][1:])

            param.restoreState(param_tmp.saveState())

    def quit_fun(self):
        """
            Leave the current instance of DAQ_Move_Main closing the parent widget.
        """
        # insert anything that needs to be closed before leaving
        try:
            if self.initialized_state:
                self.ui.IniStage_pb.click()

            self.parent.close()  # close the parent widget
            try:
                self.parent.parent().parent().close()  # the dock parent (if any)
            except Exception as e:
                self.logger.info('No dock parent to close')

        except Exception as e:
            icon = QtGui.QIcon()
            icon.addPixmap(QtGui.QPixmap(":/Labview_icons/Icon_Library/close2.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
            msgBox = QtWidgets.QMessageBox(parent=None)
            msgBox.addButton(QtWidgets.QMessageBox.Yes)
            msgBox.addButton(QtWidgets.QMessageBox.No)
            msgBox.setWindowTitle("Error")
            msgBox.setText(str(e) + " error happened when uninitializing the stage.\nDo you still want to quit?")
            msgBox.setDefaultButton(QtWidgets.QMessageBox.Yes)
            ret = msgBox.exec()
            if ret == QtWidgets.QMessageBox.Yes:
                self.parent.close()

    @Slot()
    def raise_timeout(self):
        """
            Update status with "Timeout occured" statement.

            See Also
            --------
            update_status
        """
        self.update_status("Timeout occured", wait_time=self.wait_time)
        self.wait_position_flag = False

    def set_enabled_move_buttons(self, enable=False):
        """
            Set the move buttons enabled (or not) in User Interface from the gridLayout_buttons course.

            =============== ========== ================================================
            **Parameters**   **Type**   **Description**

             *enable*        boolean    The parameter making enable or not the buttons
            =============== ========== ================================================

        """
        Nchildren = self.ui.gridLayout_buttons.count()
        for ind in range(Nchildren):
            widget = self.ui.gridLayout_buttons.itemAt(ind).widget()
            if widget is not None:
                widget.setEnabled(enable)
        self.ui.Moveto_pb_bis.setEnabled(enable)
        self.ui.Abs_position_sb_bis.setEnabled(enable)
        self.ui.Current_position_sb.setEnabled(enable)

    @Slot(int)
    def set_setting_tree(self, index=0):
        """
            Set the move settings parameters tree, clearing the current tree and setting the 'move_settings' node.

            See Also
            --------
            update_status
        """
        self.stage_name = self.ui.Stage_type_combo.currentText()
        self.settings.child('main_settings', 'move_type').setValue(self.stage_name)
        try:
            for child in self.settings.child(('move_settings')).children():
                child.remove()
            parent_module = utils.find_dict_in_list_from_key_val(DAQ_Move_Stage_type, 'name', self.stage_name)
            class_ = getattr(getattr(parent_module['module'], 'daq_move_' + self.stage_name),
                             'DAQ_Move_' + self.stage_name)
            params = getattr(class_, 'params')
            move_params = Parameter.create(name='move_settings', type='group', children=params)

            self.settings.child(('move_settings')).addChildren(move_params.children())

        except Exception as e:
            self.logger.exception(str(e))

    def show_fine_tuning(self):
        """
          Make GroupBox visible if User Interface corresponding attribute is checked to show fine tuning in.
        """
        if self.ui.fine_tuning_pb.isChecked():
            self.ui.groupBox.show()
        else:
            self.ui.groupBox.hide()

    def show_settings(self):
        """
          Make settings tree visible if User Interface corresponding attribute is checked to show the settings tree in.
        """
        if self.ui.parameters_pb.isChecked():

            self.ui.settings_tree.setVisible(True)
        else:
            self.ui.settings_tree.setVisible(False)

    @Slot(int)
    def stage_changed(self, index=0):

        """

            See Also
            --------
            move_Abs
        """
        pass

    def stop_Motion(self):
        """
            stop any motion via the launched thread with the "stop_Motion" Thread Command.

            See Also
            --------
            update_status, DAQ_utils.ThreadCommand
        """
        try:
            self.command_stage.emit(ThreadCommand(command="stop_Motion"))
        except Exception as e:
            self.logger.exception(str(e))

    @Slot(ThreadCommand)
    def thread_status(self, status):  # general function to get datas/infos from all threads back to the main
        """
            | General function to get datas/infos from all threads back to the main0
            |

            Interpret a command from the command given by the ThreadCommand status :
                * In case of **'Update_status'** command, call the update_status method with status attributes as parameters
                * In case of **'ini_stage'** command, initialise a Stage from status attributes
                * In case of **'close'** command, close the launched stage thread
                * In case of **'check_position'** command, set the Current_position value from status attributes
                * In case of **'move_done'** command, set the Current_position value, make profile of move_done and send the move done signal with status attributes
                * In case of **'Move_Not_Done'** command, set the current position value from the status attributes, make profile of Not_Move_Done and send the Thread Command "Move_abs"
                * In case of **'update_settings'** command, create child "Move Settings" from  status attributes (if possible)

            ================ ================= ======================================================
            **Parameters**     **Type**         **Description**

            *status*          ThreadCommand()   instance of ThreadCommand containing two attributes :

                                                 * *command*    str
                                                 * *attributes* list

            ================ ================= ======================================================

            See Also
            --------
            update_status, set_enabled_move_buttons, get_position, DAQ_utils.ThreadCommand, parameter_tree_changed, raise_timeout
        """

        if status.command == "Update_Status":
            if len(status.attributes) > 2:
                self.update_status(status.attributes[0], wait_time=self.wait_time, log_type=status.attributes[1])
            else:
                self.update_status(status.attributes[0], wait_time=self.wait_time)

        elif status.command == "ini_stage":
            # status.attributes[0]=edict(initialized=bool,info="", controller=)
            self.update_status("Stage initialized: {:} info: {:}".format(status.attributes[0]['initialized'],
                                                                         status.attributes[0]['info']),
                               wait_time=self.wait_time)
            if status.attributes[0]['initialized']:
                self.controller = status.attributes[0]['controller']
                self.set_enabled_move_buttons(enable=True)
                self.ui.Ini_state_LED.set_as_true()
                self.initialized_state = True
            else:
                self.initialized_state = False
            if self.initialized_state:
                self.get_position()
            self.init_signal.emit(self.initialized_state)

        elif status.command == "close":
            try:
                self.update_status(status.attributes[0], wait_time=self.wait_time)
                self.stage_thread.exit()
                self.stage_thread.wait()
                finished = self.stage_thread.isFinished()
                if finished:
                    pass
                    delattr(self, 'stage_thread')
                else:
                    self.update_status('thread is locked?!', self.wait_time, 'log')
            except Exception as e:
                self.logger.exception(str(e))
            self.initialized_state = False
            self.init_signal.emit(self.initialized_state)

        elif status.command == "check_position":
            self.ui.Current_position_sb.setValue(status.attributes[0])
            self.current_position = status.attributes[0]
            if self.settings.child('main_settings', 'tcpip', 'tcp_connected').value() and self.send_to_tcpip:
                self.command_tcpip.emit(ThreadCommand('position_is', status.attributes))

        elif status.command == "move_done":
            self.ui.Current_position_sb.setValue(status.attributes[0])
            self.current_position = status.attributes[0]
            self.move_done_bool = True
            self.ui.Move_Done_LED.set_as_true()
            self.move_done_signal.emit(self.title, status.attributes[0])
            if self.settings.child('main_settings', 'tcpip', 'tcp_connected').value() and self.send_to_tcpip:
                self.command_tcpip.emit(ThreadCommand('move_done', status.attributes))

        elif status.command == "Move_Not_Done":
            self.ui.Current_position_sb.setValue(status.attributes[0])
            self.current_position = status.attributes[0]
            self.move_done_bool = False
            self.ui.Move_Done_LED.set_as_false()
            self.command_stage.emit(ThreadCommand(command="move_Abs", attributes=[self.target_position]))

        elif status.command == 'update_main_settings':
            # this is a way for the plugins to update main settings of the ui (solely values, limits and options)
            try:
                if status.attributes[2] == 'value':
                    self.settings.child('main_settings', *status.attributes[0]).setValue(status.attributes[1])
                elif status.attributes[2] == 'limits':
                    self.settings.child('main_settings', *status.attributes[0]).setLimits(status.attributes[1])
                elif status.attributes[2] == 'options':
                    self.settings.child('main_settings', *status.attributes[0]).setOpts(**status.attributes[1])
            except Exception as e:
                self.logger.exception(str(e))

        elif status.command == 'update_settings':
            # ThreadCommand(command='update_settings',attributes=[path,data,change]))
            try:
                self.settings.sigTreeStateChanged.disconnect(
                    self.parameter_tree_changed)  # any changes on the settings will update accordingly the detector
            except Exception:
                pass
            try:
                if status.attributes[2] == 'value':
                    self.settings.child('move_settings', *status.attributes[0]).setValue(status.attributes[1])
                elif status.attributes[2] == 'limits':
                    self.settings.child('move_settings', *status.attributes[0]).setLimits(status.attributes[1])
                elif status.attributes[2] == 'options':
                    self.settings.child('move_settings', *status.attributes[0]).setOpts(**status.attributes[1])
                elif status.attributes[2] == 'childAdded':
                    child = Parameter.create(name='tmp')
                    child.restoreState(status.attributes[1][0])
                    self.settings.child('move_settings', *status.attributes[0]).addChild(status.attributes[1][0])

            except Exception as e:
                self.logger.exception(str(e))
            self.settings.sigTreeStateChanged.connect(
                self.parameter_tree_changed)  # any changes on the settings will update accordingly the detector

        elif status.command == 'raise_timeout':
            self.raise_timeout()

        elif status.command == 'outofbounds':
            self.bounds_signal.emit(True)

        elif status.command == 'show_splash':
            self.ui.settings_tree.setEnabled(False)
            self.splash_sc.show()
            self.splash_sc.raise_()
            self.splash_sc.showMessage(status.attributes[0], color=Qt.white)

        elif status.command == 'close_splash':
            self.splash_sc.close()
            self.ui.settings_tree.setEnabled(True)

        elif status.command == 'set_allowed_values':
            if 'decimals' in status.attributes:
                self.ui.Current_position_sb.setDecimals(status.attributes['decimals'])
                self.ui.Abs_position_sb.setDecimals(status.attributes['decimals'])
                self.ui.Abs_position_sb_bis.setDecimals(status.attributes['decimals'])
            if 'minimum'in status.attributes:
                self.ui.Current_position_sb.setMinimum(status.attributes['minimum'])
                self.ui.Abs_position_sb.setMinimum(status.attributes['minimum'])
                self.ui.Abs_position_sb_bis.setMinimum(status.attributes['minimum'])
            if 'maximum'in status.attributes:
                self.ui.Current_position_sb.setMaximum(status.attributes['maximum'])
                self.ui.Abs_position_sb.setMaximum(status.attributes['maximum'])
                self.ui.Abs_position_sb_bis.setMaximum(status.attributes['maximum'])
            if 'step'in status.attributes:
                self.ui.Current_position_sb.setSingleStep(status.attributes['step'])
                self.ui.Abs_position_sb.setSingleStep(status.attributes['step'])
                self.ui.Abs_position_sb_bis.setSingleStep(status.attributes['step'])

    def update_status(self, txt, wait_time=0):
        """
            Show the given txt message in the status bar with a delay of wait_time ms if specified (0 by default).

            ================ ========== =================================
            **Parameters**    **Type**   **Description**
             *txt*            string     The message to show
             *wait_time*      int        The delay time of showing
            ================ ========== =================================

        """

        self.ui.statusbar.showMessage(txt, wait_time)
        self.status_signal.emit(txt)
        self.logger.info(txt)


class DAQ_Move_stage(QObject):
    """
        ================== ========================
        **Attributes**      **Type**
        *status_sig*        instance of Signal
        *hardware*          ???
        *stage_name*        string
        *current_position*  float
        *target_position*   float
        *hardware_adress*   string
        *axis_address*      string
        *motion_stoped*     boolean
        ================== ========================
    """
    status_sig = Signal(ThreadCommand)

    def __init__(self, stage_name, position, title='actuator'):
        super().__init__()
        self.logger = utils.set_logger(f'{logger.name}.{title}.actuator')
        self.title = title
        self.hardware = None
        self.stage_name = stage_name
        self.current_position = position
        self.target_position = 0
        self.hardware_adress = None
        self.axis_address = None
        self.motion_stoped = False

    def close(self):
        """
            Uninitialize the stage closing the hardware.

        """
        self.hardware.close()

        return "Stage uninitialized"

    def check_position(self):
        """
            Get the current position checking the harware position.

        """
        pos = self.hardware.check_position()
        return pos

    def ini_stage(self, params_state=None, controller=None):
        """
            Init a stage updating the hardware and sending an hardware move_done signal.

            =============== =================================== ==========================================================================================================================
            **Parameters**   **Type**                             **Description**

             *params_state*  ordered dictionnary list             The parameter state of the hardware class composed by a list representing the tree to keep a temporary save of the tree

             *controller*    one or many instance of DAQ_Move     The controller id of the hardware

             *stage*         instance of DAQ_Move                 Defining axes and motors
            =============== =================================== ==========================================================================================================================

            See Also
            --------
            DAQ_utils.ThreadCommand, DAQ_Move
        """

        status = edict(initialized=False, info="")
        try:
            parent_module = utils.find_dict_in_list_from_key_val(DAQ_Move_Stage_type, 'name', self.stage_name)
            class_ = getattr(getattr(parent_module['module'], 'daq_move_' + self.stage_name),
                             'DAQ_Move_' + self.stage_name)
            self.hardware = class_(self, params_state)
            status.update(self.hardware.ini_stage(controller))  # return edict(info="", controller=, stage=)

            self.hardware.Move_Done_signal.connect(self.Move_Done)

            # status.initialized=True
            return status
        except Exception as e:
            self.logger.exception(str(e))
            return status

    def move_Abs(self, position, polling=True):
        """
            Make the hardware absolute move from the given position.

            =============== ========= =======================
            **Parameters**  **Type**   **Description**

            *position*       float     The absolute position
            =============== ========= =======================

            See Also
            --------
            move_Abs
        """
        position = float(position)  # because it may be a numpy float and could cause issues
        # see https://github.com/pythonnet/pythonnet/issues/1833
        self.target_position = position
        self.hardware.move_is_done = False
        self.hardware.ispolling = polling
        pos = self.hardware.move_Abs(position)
        self.hardware.poll_moving()

    def move_Rel(self, rel_position, polling=True):
        """
            Make the hardware relative move from the given relative position added to the current one.

            ================ ========= ======================
            **Parameters**   **Type**  **Description**

             *position*       float    The relative position
            ================ ========= ======================

            See Also
            --------
            move_Rel
        """
        rel_position = float(rel_position)  # because it may be a numpy float and could cause issues
        # see https://github.com/pythonnet/pythonnet/issues/1833
        self.hardware.move_is_done = False
        self.target_position = self.current_position + rel_position
        self.hardware.ispolling = polling
        pos = self.hardware.move_Rel(rel_position)
        self.hardware.poll_moving()

    @Slot(float)
    def Move_Stoped(self, pos):
        """
            Send a "move_done" Thread Command with the given position as an attribute.

            See Also
            --------
            DAQ_utils.ThreadCommand
        """
        self.status_sig.emit(ThreadCommand("move_done", [pos]))

    def move_Home(self):
        """
            Make the hardware move to the init position.

        """
        self.hardware.move_is_done = False
        self.target_position = 0
        self.hardware.move_Home()

    @Slot(float)
    def Move_Done(self, pos):
        """
            | Send a "move_done" Thread Command with the given position as an attribute and update the current position attribute.
            |
            | Check if position reached within epsilon => not necessary this is done within the hardware code see polling for instance

            See Also
            --------
            DAQ_utils.ThreadCommand
        """

        # check if position reached within epsilon=> not necessary this is done within the hardware code see polling for instance
        self.current_position = pos
        self.status_sig.emit(ThreadCommand(command="move_done", attributes=[pos]))
        # if self.motion_stoped:
        #    self.status_sig.emit(ThreadCommand(command="move_done",attributes=[pos]))
        # else:
        #    if np.abs(self.target_position-pos)>self.hardware.settings.child(('epsilon')).value():
        #        self.status_sig.emit(ThreadCommand("Move_Not_Done",[pos]))
        #    else:
        #        self.status_sig.emit(ThreadCommand("move_done",[pos]))

    @Slot(ThreadCommand)
    def queue_command(self, command=ThreadCommand()):
        """
            Interpret the given Thread Command.
                * In case of **'ini_stage'** command, init a stage from command attributes.
                * In case of **'close'** command, unitinalise the stage closing hardware and emitting the corresponding status signal
                * In case of **'move_Abs'** command, call the move_Abs method with position from command attributes
                * In case of **'move_Rel'** command, call the move_Rel method with the relative position from the command attributes.
                * In case of **'move_Home'** command, call the move_Home method
                * In case of **'check_position'** command, get the current position from the check_position method
                * In case of **'Stop_motion'** command, stop any motion via the stop_Motion method
                * In case of **'Reset_Stop_Motion'** command, set the motion_stopped attribute to false

            =============== =============== ================================
            **Parameters**   **Type**        **Description**

             *command*      ThreadCommand()   The command to be interpreted
            =============== =============== ================================

            See Also
            --------
            DAQ_utils.ThreadCommand, ini_stage, close, move_Abs, move_Rel, move_Home, check_position, stop_Motion
        """
        try:
            if command.command == "ini_stage":
                status = self.ini_stage(
                    *command.attributes)  # return edict(initialized=bool,info="", controller=, stage=)
                self.status_sig.emit(ThreadCommand(command=command.command, attributes=[status, 'log']))

            elif command.command == "close":
                status = self.close()
                self.status_sig.emit(ThreadCommand(command=command.command, attributes=[status]))

            elif command.command == "move_Abs":
                self.move_Abs(*command.attributes)

            elif command.command == "move_Rel":
                self.move_Rel(*command.attributes)

            elif command.command == "move_Home":
                self.move_Home()

            elif command.command == "check_position":
                pos = self.check_position()

            elif command.command == "stop_Motion":
                self.stop_motion()

            elif command.command == "Reset_Stop_Motion":
                self.motion_stoped = False

            else:  # custom commands for particular plugins (see spectrometer module 'get_spectro_wl' for instance)
                if hasattr(self.hardware, command.command):
                    cmd = getattr(self.hardware, command.command)
                    cmd(*command.attributes)
        except Exception as e:
            self.logger.exception(str(e))

    def stop_motion(self):
        """
            stop hardware motion with motion_stopped attribute updtaed to True and a status signal sended with an "update_status" Thread Command

            See Also
            --------
            DAQ_utils.ThreadCommand, stop_Motion
        """
        self.status_sig.emit(ThreadCommand(command="Update_Status", attributes=["Motion stoping", 'log']))
        self.motion_stoped = True
        self.hardware.stop_motion()
        self.hardware.poll_timer.stop()

    @Slot(edict)
    def update_settings(self, settings_parameter_dict):
        """
            Update settings of hardware with dictionnary parameters in case of "Move_Settings" path, else update attributes with dictionnary parameters.

            =========================  =========== ======================================================
            **Parameters**              **Type**    **Description**

            *settings_parameter_dict*  dictionnary  Dictionnary containing the path and linked parameter
            =========================  =========== ======================================================

            See Also
            --------
            update_settings
        """
        # settings_parameter_dict = edict(path=path,param=param)
        path = settings_parameter_dict['path']
        param = settings_parameter_dict['param']
        if path[0] == 'main_settings':
            if hasattr(self, path[-1]):
                setattr(self, path[-1], param.value())

        elif path[0] == 'move_settings':
            self.hardware.update_settings(settings_parameter_dict)


def main(init_qt=True):
    if init_qt: # used for the test suite
        app = QtWidgets.QApplication(sys.argv)

    form = QtWidgets.QWidget()
    prog = DAQ_Move(form, title="test", init=False)
    form.show()

    if init_qt:
        sys.exit(app.exec_())
    return prog, form

if __name__ == '__main__':
    main()
