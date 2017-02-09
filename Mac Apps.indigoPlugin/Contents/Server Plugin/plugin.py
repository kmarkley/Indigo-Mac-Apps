#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################
# http://www.indigodomo.com

import indigo
import time
from datetime import datetime, timedelta
import re
import subprocess
try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote

# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.

###############################################################################
# globals

k_processStatusDict = { 'I': {'t': 'idle',      'i': indigo.kStateImageSel.AvPaused         },
                        'R': {'t': 'running',   'i': indigo.kStateImageSel.SensorOn         },
                        'S': {'t': 'running',   'i': indigo.kStateImageSel.SensorOn         },
                        'T': {'t': 'stopped',   'i': indigo.kStateImageSel.AvStopped        },
                        'U': {'t': 'waiting',   'i': indigo.kStateImageSel.AvPaused         },
                        'Z': {'t': 'zombie',    'i': indigo.kStateImageSel.SensorTripped    },
                        'X': {'t': 'off',       'i': indigo.kStateImageSel.SensorOff        }   }

k_psGetDataCmd      = "ps -awxc -opid,state,pcpu,pmem,lstart,etime,args"
k_psInfoGroupsKeys  =           (      'pid',    'state',             'pcpu',     'pmem',   'lstart',  'etime',   'args' )
k_psDataGroupsRegex = re.compile(r"^ *([0-9]+) +([IRSTUZ])[sA-Z+<>]* +([0-9.,]+) +([0-9.,]+) +(.+?)   +([0-9:-]+) +(.+)$")

k_psSearch_pid      = "^ *{pid} .*$".format
k_psSearch_appname  = "^.*[0-9] {processname}( -psn_[0-9_]*)?$".format
k_psSearch_helper   = "^.*[0-9] {processname} ?( -.+)*$".format
k_psSearch_daemon   = "^.*[0-9] {processname} ?{args}$".format

k_appOpenCmd        = "open{background}{fresh} {apppath}".format
k_appQuitCmd        = "osascript -e 'tell application \"{processname}\" to quit'".format
k_daemonStartCmd    = "launchctl submit -l {processname} -- {apppath} {args}".format
k_daemonStopCmd     = "launchctl remove {processname}".format
k_killCmd           = "kill {pid}".format

k_returnFalseCmd    = "echo {message}; false".format

k_awkSumColumn      = "echo {data} | awk '{{s+=${col}}} END {{print s}}'".format


################################################################################
class Plugin(indigo.PluginBase):
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
    
    def __del__(self):
        indigo.PluginBase.__del__(self)

    ########################################
    # Start, Stop and Config changes
    ########################################
    def startup(self):
        
        self.stateLoopFreq  = int(self.pluginPrefs.get('stateLoopFreq','10'))
        self.pushStatsFreq  = int(self.pluginPrefs.get('pushStatsFreq','30'))
        self.cores          = float(self.pluginPrefs.get('cores','1'))
        self.debug          = self.pluginPrefs.get('showDebugInfo',False)
        self.logger.debug("startup")
        if self.debug:
            self.logger.debug("Debug logging enabled")
        
        self.deviceDict = dict()
        self._psData = ""
        self._psTime = 0
        self.ps_refresh = True

    ########################################
    def shutdown(self):
        self.logger.debug("shutdown")
        self.pluginPrefs["showDebugInfo"] = self.debug

    ########################################
    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        self.logger.debug("closedPrefsConfigUi")
        if not userCancelled:
            self.stateLoopFreq  = int(valuesDict['stateLoopFreq'])
            self.pushStatsFreq  = int(valuesDict['pushStatsFreq'])
            self.cores          = float(valuesDict['cores'])
            self.debug          = valuesDict['showDebugInfo']
            if self.debug:
                self.logger.debug("Debug logging enabled")

    ########################################
    def validatePrefsConfigUi(self, valuesDict):
        self.logger.debug("validatePrefsConfigUi")
        errorsDict = indigo.Dict()
        
        if not valuesDict.get('cores',False):
            valuesDict['cores'] = '1'
        if not valuesDict['cores'].isdigit() or not (int(valuesDict['cores']) >= 1):
            errorsDict['cores'] = "Must be a positive integer"
                
        if len(errorsDict) > 0:
            self.logger.debug('validate prefs config error: \n{0}'.format(str(errorsDict)))
            return (False, valuesDict, errorsDict)
        return (True, valuesDict)
    
    ########################################
    def runConcurrentThread(self):
        lastPushStats = 0
        self.sleep(self.stateLoopFreq)
        try:
            while True:
                loopStart = time.time()
                doPushStats = loopStart >= lastPushStats  + self.pushStatsFreq
                
                self.ps_refresh = True
                for devId, dev in self.deviceDict.items():
                    dev.update(doPushStats)
                
                lastPushStats = [lastPushStats, loopStart][doPushStats]
                
                self.sleep( loopStart + self.stateLoopFreq - time.time() )
        except self.StopThread:
            pass    # Optionally catch the StopThread exception and do any needed cleanup.
    
    
    ########################################
    @property
    def psResults(self):
        if self.ps_refresh:
            success, data = do_shell_script(k_psGetDataCmd)
            if success:
                self._psData = data
                self._psTime = time.time()
                self.ps_refresh = False
        return self._psData, self._psTime
    
    ########################################
    # Device Methods
    ########################################
    def deviceStartComm(self, dev):
        self.logger.debug("deviceStartComm: "+dev.name)
        if dev.version != self.pluginVersion:
            self.updateDeviceVersion(dev)
        if dev.configured:
            if dev.deviceTypeId == 'sysload':
                self.deviceDict[dev.id] = self.SystemLoadDevice(dev, self)
            else:
                self.deviceDict[dev.id] = self.ApplicationDevice(dev, self)
            self.deviceDict[dev.id].update(True)
    
    ########################################
    def deviceStopComm(self, dev):
        self.logger.debug("deviceStopComm: "+dev.name)
        if dev.id in self.deviceDict:
            del self.deviceDict[dev.id]
    
    ########################################
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
    
    ########################################
    def updateDeviceVersion(self, dev):
        theProps = dev.pluginProps
        # update states
        dev.stateListOrDisplayStateIdChanged()
        # check for props
        
        # push to server
        theProps["version"] = self.pluginVersion
        dev.replacePluginPropsOnServer(theProps)
    
    
    ########################################
    # Action Methods
    ########################################
    def actionControlDimmerRelay(self, action, dev):
        self.logger.debug("actionControlDimmerRelay: "+dev.name)
        app = self.deviceDict[dev.id]
        # TURN ON
        if action.deviceAction == indigo.kDimmerRelayAction.TurnOn:
            app.onState = True
        # TURN OFF
        elif action.deviceAction == indigo.kDimmerRelayAction.TurnOff:
            app.onState = False
        # TOGGLE
        elif action.deviceAction == indigo.kDimmerRelayAction.Toggle:
            app.onState = not app.onState
        # STATUS REQUEST
        elif action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.logger.info('"{0}" status update'.format(dev.name))
            self.ps_refresh = True
            app.update(True)
        # UNKNOWN
        else:
            self.logger.debug('"{0}" {1} request ignored'.format(dev.name, str(action.deviceAction)))
    
    ########################################
    # Menu Methods
    ########################################
    def toggleDebug(self):
        if self.debug:
            self.logger.debug("Debug logging disabled")
            self.debug = False
        else:
            self.debug = True
            self.logger.debug("Debug logging enabled")
    
    
    ########################################
    # Classes
    ########################################
    ########################################
    class ApplicationDevice(object):
        ########################################
        def __init__(self, instance, plugin):
            self.dev        = instance
            self.name       = self.dev.name
            self.type       = self.dev.deviceTypeId
            self.props      = self.dev.pluginProps
            self.states     = self.dev.states
            self.status     = 'X'
            
            self.plugin     = plugin
            self.logger     = plugin.logger
            
            self._psInfo    = ""
            self._psTime    = 0
            self._psRegex = ""
            self.psRegex_refresh  = True
        

        ########################################
        def update(self, doStats=False):
            self.states['onOffState'] = bool(self.psInfo)
            
            if doStats:
                if self.onState:
                    stats = re_extract(self.psInfo, k_psDataGroupsRegex, k_psInfoGroupsKeys)
                    self.status                 = stats['state']
                    self.states['process_id']   = stats['pid']
                    self.states['last_start']   = lstart_to_timestamp(stats['lstart'])
                    self.states['elapsed_time'] = stats['etime']
                    self.states['elapsed_secs'] = etime_to_seconds(stats['etime'])
                    self.states['percent_cpu']  = float(stats['pcpu'])/self.plugin.cores
                    self.states['percent_mem']  = float(stats['pmem'])
                
                else:
                    self.status                 = 'X'
                    self.states['process_id']   = ''
                    self.states['elapsed_time'] = ''
                    self.states['elapsed_secs'] = 0
                    self.states['percent_cpu']  = 0.0
                    self.states['percent_mem']  = 0.0
            
                self.states['process_status']   = k_processStatusDict[self.status]['t']
            
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
                        self.dev.updateStateImageOnServer(k_processStatusDict[self.status]['i'])
                    elif key == 'process_id':
                        self.psRegex_refresh = True
                
            if len(newStates) > 0:
                if self.plugin.debug: # don't fill up plugin log unless actively debugging
                    self.logger.debug('updating states on device "{0}":'.format(self.name))
                    for item in newStates:
                        self.logger.debug('{:>16}: {}'.format(item['key'],item['value']))
                self.dev.updateStatesOnServer(newStates)
                self.states = self.dev.states
        
        ########################################
        # Class Properties
        ########################################
        def onStateGet(self):
            return self.states['onOffState']
        
        def onStateSet(self,newState):
            if newState != self.onState:
                success, result = do_shell_script(self.onOffCmds[newState])
                if success:
                    self.logger.info('{0} {1} "{2}"'.format(['quitting','launching'][newState], self.type, self.props['applicationName']))
                    self.plugin.ps_refresh = True
                    self.psRegex_refresh = True
                    self.plugin.sleep(0.25)
                    self.update(True)
                else:
                    self.logger.error('failed to {0} {1} "{2}"'.format(['quit','launch'][newState], self.type, self.props['applicationName']))
                    self.logger.debug(response)
        
        onState = property(onStateGet, onStateSet)
        
        ########################################
        @property
        def onOffCmds(self):
            onCmd = offCmd = k_returnFalseCmd(message = "command not configured")
            if self.type in ['application','helper']:
                onCmd = k_appOpenCmd(       background  = ['',' -g'][self.props.get('openBackground',True)],
                                            fresh       = ['',' -F'][not self.props.get('restoreState',False)],
                                            apppath     = cmd_quote(self.props['applicationPath']) )
            elif self.type =='daemon':
                onCmd = k_daemonStartCmd(   processname = cmd_quote(self.props['processName']),
                                            apppath     = cmd_quote(self.props['applicationPath']),
                                            args        = cmd_quote(self.props['startArgs']) )
            
            if self.props['forceQuit']:
                if self.states['process_id']:
                    offCmd = k_killCmd(     pid         = self.states['process_id'] )
            else:
                if self.type == 'application':
                    offCmd = k_appQuitCmd(  processname = self.props['processName'] )
                elif self.type == 'helper':
                    offCmd = k_killCmd(     pid         = self.states['process_id'] )
                elif self.type =='daemon':
                    offCmd = k_daemonStopCmd(processname = cmd_quote(self.props['processName']) )
            
            return (offCmd,onCmd)
        
        ########################################
        @property
        def psInfo(self):
            psData, psTime = self.plugin.psResults
            if psTime > self._psTime:
                match = self.psRegex.search(psData)
                if not match and self.states['process_id']:
                    # perhaps pid is out of date (restarted between checks)
                    # try again using process name
                    self.states['process_id'] = ''
                    self.psRegex_refresh = True
                    match = self.psRegex.search(psData)
                if match:
                    self._psInfo = match.group(0)
                else:
                    self._psInfo = False
                self._psTime = psTime
            return self._psInfo
    
        @property
        def psRegex(self):
            if self.psRegex_refresh:
                if self.states['process_id']:
                    exp = k_psSearch_pid( pid = self.states['process_id'] )
                else:
                    if self.type =='application':
                        exp = k_psSearch_appname(   processname = self.props['processName'] )
                    elif self.type =='helper':
                        exp = k_psSearch_helper(    processname = self.props['processName'] )
                    elif self.type == 'daemon':
                        exp = k_psSearch_daemon(    processname = self.props['processName'],
                                                    args        = self.props['startArgs']   )
                self._psRegex = re.compile( exp, re.MULTILINE )
                self.psRegex_refresh = False
            return self._psRegex
        
    
    ########################################
    ########################################
    class SystemLoadDevice(object):
        ########################################
        def __init__(self, instance, plugin):
            self.dev        = instance
            self.name       = self.dev.name
            self.type       = self.dev.deviceTypeId
            self.props      = self.dev.pluginProps
            self.states     = self.dev.states
            
            self.plugin     = plugin
            self.logger     = plugin.logger
            
            self._psTime    = 0
        

        ########################################
        def update(self, doStats=False):
            self.updateOnOff()
            if doStats:
                self.updateStats()
            self.saveStates()
        
        def updateOnOff(self):
            pass
        
        def updateStats(self):
            psData, psTime = self.plugin.psResults
            self.states['percent_cpu']  = float(sumColumn(psData, k_psInfoGroupsKeys.index('pcpu')+1))/self.plugin.cores
            self.states['percent_mem']  = float(sumColumn(psData, k_psInfoGroupsKeys.index('pmem')+1))
            self.states['displayState'] = "{:.1f}% | {:.1f}%".format(self.states['percent_cpu'],self.states['percent_mem'])
            
        def saveStates(self):
            newStates = []
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
        
        ########################################
        # Class Properties
        ########################################
        def onStateGet(self):
            return None
        def onStateSet(self,newState):
            self.logger.error('{0} command not supported for "{1}"'.format(['off','on'][newState], self.name))
        onState = property(onStateGet, onStateSet)
        


########################################
# Utilities
########################################
def do_shell_script(cmd):
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, err = p.communicate()
    return (not bool(p.returncode)), out.rstrip()

########################################
def re_extract(source, rule, keys):
    results = {}
    for key, value in zip(keys,rule.match(source).groups()):
        results[key] = value.strip()
    return results

########################################
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
    dtime = timedelta (days=int(days), hours=int(hours), minutes=int(minutes), seconds=int(seconds))
    return int(dtime.total_seconds())

########################################
def lstart_to_timestamp(lstart):
    return str(datetime.strptime(lstart, '%c'))

########################################
def sumColumn(data, col):
    success, result = do_shell_script(k_awkSumColumn(data=cmd_quote(data), col=col))
    if success:
        return result
    else:
        self.logger.error(result)
        return 0.0