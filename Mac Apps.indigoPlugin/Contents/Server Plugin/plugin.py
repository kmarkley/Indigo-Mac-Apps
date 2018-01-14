#! /usr/bin/env python
# -*- coding: utf-8 -*-
###############################################################################
# http://www.indigodomo.com

import indigo
import time
from datetime import datetime
import re
import subprocess
try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote
from ghpu import GitHubPluginUpdater

# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.

###############################################################################
# globals

k_processStatusDict = { 'I': {'txt': 'idle',      'img': indigo.kStateImageSel.AvPaused         },
                        'R': {'txt': 'running',   'img': indigo.kStateImageSel.SensorOn         },
                        'S': {'txt': 'running',   'img': indigo.kStateImageSel.SensorOn         },
                        'T': {'txt': 'stopped',   'img': indigo.kStateImageSel.AvStopped        },
                        'U': {'txt': 'waiting',   'img': indigo.kStateImageSel.AvPaused         },
                        'Z': {'txt': 'zombie',    'img': indigo.kStateImageSel.SensorTripped    },
                        'X': {'txt': 'off',       'img': indigo.kStateImageSel.SensorOff        }   }

k_psGetDataCmd      = "/bin/ps -awxc -opid,state,pcpu,pmem,lstart,etime,args"
k_psInfoGroupsKeys  =           (      'pid',    'state',             'pcpu',     'pmem',   'lstart',  'etime',   'args' )
k_psInfoGroupsRegex = re.compile(r"^ *([0-9]+) +([IRSTUZ])[sA-Z+<>]* +([0-9.,]+) +([0-9.,]+) +(.+?)   +([0-9:-]+) +(.+)$")

k_psSearch_pid      = "^ *{pid} .*$".format
k_psSearch_appname  = "^.*[0-9] {processname}( -psn_[0-9_]*)?$".format
k_psSearch_helper   = "^.*[0-9] {processname} ?( -.+)*$".format
k_psSearch_daemon   = "^.*[0-9] {processname} ?{args}$".format

k_appOpenCmd        = "/usr/bin/open{background}{fresh} {apppath}".format
k_appQuitCmd        = "/usr/bin/osascript -e 'tell application \"{processname}\" to quit'".format
k_daemonStartCmd    = "/bin/launchctl submit -l {processname} -- {apppath} {args}".format
k_daemonStopCmd     = "/bin/launchctl remove {processname}".format
k_killCmd           = "/bin/kill {pid}".format

k_returnFalseCmd    = "/bin/echo {message}; false".format

k_awkSumColumn      = "/bin/echo {data} | /usr/bin/awk '{{s+=${col}}} END {{print s}}'".format

k_countCoresCmd     = "/usr/sbin/sysctl -n hw.ncpu"

k_updateCheckHours  = 24

################################################################################
class Plugin(indigo.PluginBase):
    #------------------------------------------------------------------------------
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.updater    = GitHubPluginUpdater(self)
        self.deviceDict = dict()

    def __del__(self):
        indigo.PluginBase.__del__(self)

    #------------------------------------------------------------------------------
    # Start, Stop and Config changes
    #------------------------------------------------------------------------------
    def startup(self):

        self.stateLoopFreq  = int(self.pluginPrefs.get('stateLoopFreq','10'))
        self.pushStatsFreq  = int(self.pluginPrefs.get('pushStatsFreq','30'))
        self.cores          = countCores()
        self.divisor        = [1.,self.cores][self.pluginPrefs.get('divideByCores',True)]
        self.nextCheck      = self.pluginPrefs.get('nextUpdateCheck',0)
        self.debug          = self.pluginPrefs.get('showDebugInfo',False)
        self.logger.debug("startup")
        if self.debug:
            self.logger.debug("Debug logging enabled")

        self.deviceDict = dict()

        self._psData = ""
        self._psRefresh = True

    #------------------------------------------------------------------------------
    def shutdown(self):
        self.logger.debug("shutdown")
        self.pluginPrefs["showDebugInfo"] = self.debug
        self.pluginPrefs['nextUpdateCheck'] = self.nextCheck

    #------------------------------------------------------------------------------
    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        self.logger.debug("closedPrefsConfigUi")
        if not userCancelled:
            self.stateLoopFreq  = int(valuesDict['stateLoopFreq'])
            self.pushStatsFreq  = int(valuesDict['pushStatsFreq'])
            self.divisor        = [1.,self.cores][valuesDict['divideByCores']]
            self.logger.debug("divisor: "+str(self.divisor))
            self.debug          = valuesDict['showDebugInfo']
            if self.debug:
                self.logger.debug("Debug logging enabled")

    #------------------------------------------------------------------------------
    def validatePrefsConfigUi(self, valuesDict):
        self.logger.debug("validatePrefsConfigUi")
        errorsDict = indigo.Dict()

        if len(errorsDict) > 0:
            self.logger.debug('validate prefs config error: \n{0}'.format(str(errorsDict)))
            return (False, valuesDict, errorsDict)
        return (True, valuesDict)

    #------------------------------------------------------------------------------
    def runConcurrentThread(self):
        lastPushStats = 0
        self.sleep(self.stateLoopFreq)
        try:
            while True:
                loopStart = time.time()
                doPushStats = loopStart >= lastPushStats  + self.pushStatsFreq

                self.refresh_data()
                for devId, dev in self.deviceDict.items():
                    dev.update(doPushStats)

                lastPushStats = [lastPushStats, loopStart][doPushStats]

                if loopStart > self.nextCheck:
                    self.checkForUpdates()
                    self.nextCheck = loopStart + k_updateCheckHours*60*60

                self.sleep( loopStart + self.stateLoopFreq - time.time() )
        except self.StopThread:
            pass    # Optionally catch the StopThread exception and do any needed cleanup.

    #------------------------------------------------------------------------------
    @property
    def psResults(self):
        if self._psRefresh:
            success, data = do_shell_script(k_psGetDataCmd)
            if success:
                self._psData = data
                self._psRefresh = False
        return self._psData

    def refresh_data(self):
        self._psRefresh = True

    #------------------------------------------------------------------------------
    # Device Methods
    #------------------------------------------------------------------------------
    def deviceStartComm(self, dev):
        self.logger.debug("deviceStartComm: "+dev.name)
        if dev.version != self.pluginVersion:
            self.updateDeviceVersion(dev)
        if dev.configured:
            if dev.deviceTypeId == 'sysload':
                self.deviceDict[dev.id] = SystemLoadDevice(dev, self)
            else:
                self.deviceDict[dev.id] = ApplicationDevice(dev, self)
            self.deviceDict[dev.id].update(True)

    #------------------------------------------------------------------------------
    def deviceStopComm(self, dev):
        self.logger.debug("deviceStopComm: "+dev.name)
        if dev.id in self.deviceDict:
            del self.deviceDict[dev.id]

    #------------------------------------------------------------------------------
    def validateDeviceConfigUi(self, valuesDict, deviceTypeId, devId, runtime=False):
        self.logger.debug("validateDeviceConfigUi: " + deviceTypeId)
        errorsDict = indigo.Dict()

        if deviceTypeId != 'sysload':
            if not valuesDict.get('applicationName',''):
                errorsDict['applicationName'] = "Required"

            if valuesDict.get('useApplicationsFolder',False):
                valuesDict['directoryPath'] = "/Applications/"
            elif not valuesDict.get('directoryPath',''):
                errorsDict['directoryPath'] = "Required"
            elif (valuesDict['directoryPath'])[-1:] != '/':
                valuesDict['directoryPath'] = valuesDict['directoryPath'] + "/"
            valuesDict['applicationPath'] = valuesDict['directoryPath'] + valuesDict['applicationName']

            if deviceTypeId == 'application':
                if (valuesDict['applicationName'])[-4:] == ".app":
                    valuesDict['applicationName'] = (valuesDict['applicationName'])[:-4]
                if (valuesDict['applicationPath'])[-4:] != ".app":
                    valuesDict['applicationPath'] = valuesDict['applicationPath'] + ".app"

            elif deviceTypeId == 'helper':
                valuesDict['forceQuit'] = True

            elif deviceTypeId == 'daemon':
                valuesDict['forceQuit'] = False

            if not valuesDict.get('useSpecialName',True) or not valuesDict.get('processName',''):
                valuesDict['processName'] = valuesDict['applicationName']

        if len(errorsDict) > 0:
            self.logger.debug('validate device config error: \n{0}'.format(str(errorsDict)))
            return (False, valuesDict, errorsDict)
        else:
            return (True, valuesDict)

    #------------------------------------------------------------------------------
    def updateDeviceVersion(self, dev):
        theProps = dev.pluginProps
        # update states
        dev.stateListOrDisplayStateIdChanged()
        # check for props

        # push to server
        theProps["version"] = self.pluginVersion
        dev.replacePluginPropsOnServer(theProps)


    #------------------------------------------------------------------------------
    # Action Methods
    #------------------------------------------------------------------------------
    def actionControlDimmerRelay(self, action, dev):
        self.logger.debug("actionControlDimmerRelay: "+dev.name)
        appDev = self.deviceDict[dev.id]
        # TURN ON
        if action.deviceAction == indigo.kDimmerRelayAction.TurnOn:
            appDev.onState = True
        # TURN OFF
        elif action.deviceAction == indigo.kDimmerRelayAction.TurnOff:
            appDev.onState = False
        # TOGGLE
        elif action.deviceAction == indigo.kDimmerRelayAction.Toggle:
            appDev.onState = not appDev.onState
        # STATUS REQUEST
        elif action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.logger.info('"{0}" status update'.format(dev.name))
            self.refresh_data()
            appDev.update(True)
        # UNKNOWN
        else:
            self.logger.debug('"{0}" {1} request ignored'.format(dev.name, str(action.deviceAction)))

    #-------------------------------------------------------------------------------
    # Menu Methods
    #-------------------------------------------------------------------------------
    def checkForUpdates(self):
        self.updater.checkForUpdate()

    #-------------------------------------------------------------------------------
    def updatePlugin(self):
        self.updater.update()

    #-------------------------------------------------------------------------------
    def forceUpdate(self):
        self.updater.update(currentVersion='0.0.0')

    #------------------------------------------------------------------------------
    def toggleDebug(self):
        if self.debug:
            self.logger.debug("Debug logging disabled")
            self.debug = False
        else:
            self.debug = True
            self.logger.debug("Debug logging enabled")

###############################################################################
# Classes
###############################################################################
class ApplicationDevice(object):

    #------------------------------------------------------------------------------
    def __init__(self, instance, plugin):
        self.dev        = instance
        self.name       = self.dev.name
        self.type       = self.dev.deviceTypeId
        self.props      = self.dev.pluginProps
        self.states     = self.dev.states
        self.status     = 'X'

        self.plugin     = plugin
        self.logger     = plugin.logger

        self._refresh   = True
        self._psInfo    = ""
        self._pid       = ""

        if self.type =='application':
            self.namePattern    = k_psSearch_appname(   processname = self.props['processName'] )
            self.onCmd          = k_appOpenCmd(         background  = ['',' -g'][self.props.get('openBackground',True)],
                                                        fresh       = ['',' -F'][self.props.get('openFresh',True)],
                                                        apppath     = cmd_quote(self.props['applicationPath']) )

        elif self.type =='helper':
            self.namePattern    = k_psSearch_helper(    processname = self.props['processName'] )
            self.onCmd          = k_appOpenCmd(         background  = ['',' -g'][self.props.get('openBackground',True)],
                                                        fresh       = ['',' -F'][self.props.get('openFresh',True)],
                                                        apppath     = cmd_quote(self.props['applicationPath']) )

        elif self.type == 'daemon':
            self.namePattern    = k_psSearch_daemon(    processname = self.props['processName'],
                                                        args        = self.props['startArgs']   )
            self.onCmd          = k_daemonStartCmd(     processname = cmd_quote(self.props['processName']),
                                                        apppath     = cmd_quote(self.props['applicationPath']),
                                                        args        = cmd_quote(self.props['startArgs']) )

    #------------------------------------------------------------------------------
    def update(self, doStats=False):
        self._refresh = True

        self.states['onOffState'] = bool(self.psInfo)

        if doStats or (self.onState != self.dev.onState):
            if self.onState:
                stats = re_extract(self.psInfo, k_psInfoGroupsRegex, k_psInfoGroupsKeys)
                self.status                 = stats['state']
                self.states['process_id']   = stats['pid']
                self.states['last_start']   = lstart_to_timestamp(stats['lstart'])
                self.states['elapsed_time'] = stats['etime']
                self.states['elapsed_secs'] = etime_to_seconds(stats['etime'])
                self.states['percent_cpu']  = float(stats['pcpu'])/self.plugin.divisor
                self.states['percent_mem']  = float(stats['pmem'])
            else:
                self.status                 = 'X'
                self.states['process_id']   = ''
                self.states['elapsed_time'] = ''
                self.states['elapsed_secs'] = 0
                self.states['percent_cpu']  = 0.0
                self.states['percent_mem']  = 0.0
            self.states['process_status']   = k_processStatusDict[self.status]['txt']

        newStates = []
        for key, value in self.states.iteritems():
            if self.states[key] != self.dev.states[key]:
                if key in ['percent_cpu','percent_mem']:
                    newStates.append({'key':key,'value':value, 'uiValue': '{0}%'.format(value), 'decimalPlaces':1})
                elif key == 'elapsed_secs':
                    newStates.append({'key':key,'value':value, 'uiValue': '{0} sec'.format(value)})
                else:
                    newStates.append({'key':key,'value':value})

                if key == 'onOffState':
                    self.logger.info('"{0}" {1}'.format(self.name, ['off','on'][value]))
                elif key == 'process_status':
                    self.dev.updateStateImageOnServer(k_processStatusDict[self.status]['img'])

        if len(newStates) > 0:
            if self.plugin.debug: # don't fill up plugin log unless actively debugging
                self.logger.debug('updating states on device "{0}":'.format(self.name))
                for item in newStates:
                    self.logger.debug('{:>16}: {}'.format(item['key'],item['value']))
            self.dev.updateStatesOnServer(newStates)
            self.states = self.dev.states

    #------------------------------------------------------------------------------
    # Class Properties
    #------------------------------------------------------------------------------
    def onStateGet(self):
        return self.states['onOffState']

    def onStateSet(self,newState):
        if newState != self.onState:
            success, response = do_shell_script([self.offCmd,self.onCmd][newState])
            if success:
                self.logger.info('{0} {1} "{2}"'.format(['quitting','launching'][newState], self.type, self.props['applicationName']))
                self.plugin.refresh_data()
                self.plugin.sleep(0.25)
                self.update(True)
            else:
                self.logger.error('failed to {0} {1} "{2}"'.format(['quit','launch'][newState], self.type, self.props['applicationName']))
                self.logger.debug(response)

    onState = property(onStateGet, onStateSet)

    #------------------------------------------------------------------------------
    @property
    def psInfo(self):
        if self._refresh:
            match = re.search(self.psPattern, self.plugin.psResults, re.MULTILINE)
            if match:
                self._psInfo = match.group(0)
            else:
                self._psInfo = ""
            self._refresh = False
        return self._psInfo

    @property
    def psPattern(self):
        if self.pid:
            return self.pidPattern
        else:
            return self.namePattern

    @property
    def pid(self):
        pid = [ "", self.states['process_id'] ][self.onState]
        if pid != self._pid:
            if pid:
                self.pidPattern = k_psSearch_pid(pid = pid)
            self._pid = pid
        return pid

    @property
    def offCmd(self):
        if not self.props['forceQuit']:
            if self.type == 'application':
                return k_appQuitCmd(processname = self.props['processName'])
            elif self.type == 'daemon':
                return k_daemonStopCmd(processname = cmd_quote(self.props['processName']))
        elif self.pid:
            return k_killCmd(pid = self.pid)
        else:
            return k_returnFalseCmd(message = "command not available")

###############################################################################
class SystemLoadDevice(object):

    #------------------------------------------------------------------------------
    def __init__(self, instance, plugin):
        self.dev        = instance
        self.name       = self.dev.name
        self.type       = self.dev.deviceTypeId
        self.props      = self.dev.pluginProps
        self.states     = self.dev.states

        self.plugin     = plugin
        self.logger     = plugin.logger

    #------------------------------------------------------------------------------
    def update(self, doStats=False):
        if doStats:
            psData = self.plugin.psResults
            self.states['percent_cpu']  = sumColumn(psData, k_psInfoGroupsKeys.index('pcpu'))/self.plugin.divisor
            self.states['percent_mem']  = sumColumn(psData, k_psInfoGroupsKeys.index('pmem'))
            self.states['displayState'] = "{:.1f}% | {:.1f}%".format(self.states['percent_cpu'],self.states['percent_mem'])

            newStates = list()
            for key, value in self.states.iteritems():
                if self.states[key] != self.dev.states[key]:
                    if key in ['percent_cpu','percent_mem']:
                        newStates.append({'key':key,'value':value, 'uiValue': '{0}%'.format(value), 'decimalPlaces':1})
                    else:
                        newStates.append({'key':key,'value':value})

            if len(newStates) > 0:
                if self.plugin.debug: # don't fill up plugin log unless actively debugging
                    self.logger.debug('updating states on device "{0}":'.format(self.name))
                    for item in newStates:
                        self.logger.debug('{:>16}: {}'.format(item['key'].rjust(16),item['value']))
                self.dev.updateStatesOnServer(newStates)
                self.states = self.dev.states

    #------------------------------------------------------------------------------
    # Class Properties
    #------------------------------------------------------------------------------
    def onStateGet(self):
        return None
    def onStateSet(self,newState):
        self.logger.error('{0} command not supported for "{1}"'.format(['off','on'][newState], self.name))
    onState = property(onStateGet, onStateSet)

###############################################################################
# Utilities
###############################################################################
def do_shell_script(cmd):
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, err = p.communicate()
    return (not bool(p.returncode)), out.rstrip()

#------------------------------------------------------------------------------
def re_extract(source, rule, keys):
    results = dict()
    for key, value in zip(keys,rule.match(source).groups()):
        results[key] = value.strip()
    return results

#------------------------------------------------------------------------------
def etime_to_seconds(etime):
    try:
        days, etime = etime.split('-')
    except:
        days = 0
    try:
        (hours,minutes,seconds) = etime.split(':')
    except:
        hours = 0
        (minutes,seconds) = etime.split(':')
    return ((int(days)*24 + int(hours))*60 + int(minutes))*60 + int(seconds)

#------------------------------------------------------------------------------
def lstart_to_timestamp(lstart):
    return str(datetime.strptime(lstart, '%c'))

#------------------------------------------------------------------------------
def sumColumn(data, col=0):
    # col argument is like index -- i.e. first column is 0
    success, result = do_shell_script(k_awkSumColumn(data=cmd_quote(data), col=col+1))
    if success:
        return float(result)
    else:
        self.logger.error(result)
        return 0.0

#------------------------------------------------------------------------------
def countCores():
    success, result = do_shell_script(k_countCoresCmd)
    if success:
        return float(result)
    else:
        self.logger.error(result)
        return 1.0
