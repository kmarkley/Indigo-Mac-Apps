# Mac Apps

This plugin uses shell commands (`ps`) to determine if processes are running on the machine running Indigo Server and obtain some statistics about them.  Processes may be launched or quit by turning the associated Indigo device on or off.

## Plugin Configuration

* **Update frequency**  
How often to check whether applications are running.

* **Push stats frequency**  
How often to updtate the statistics in the device states.  Statistics will change every time for running applications, which can mean more communication with Indigo.  Reduce this setting if you start seeing "ServerCommunication" errors in the log.

* **Divide %CPU by cores**  
By default, Macs report percent cpu of a single core. Enter the number of cores here to make the plugin report a a true percentage of total capacity.

* **Enable debugging**  
If checked, extensive debug information will be written to the log.

## Devices

### Application

This is for standard applications with a **.app** extension (likely hidden) that usually reside in the /Applications folder.

#### Configuration

* **Application name**  
The name of the application (exclude the **.app** extension).

* **Use Applications folder**  
Check if the application is in the `/Applications` folder.

* **Directory path**  
For applications **not** in the `/Applications` folder, specify the full path to the directory containing the application.

* **Open fresh**  
Check to launch the application 'fresh', without restoring previous windows and documents.

* **Open in background**  
Do not bring application to the front after launching.

* **Use force quit**  
Force the application to close even if there are unsaved changes.

* **Use special process name**  
Occaisionally applications will have a different name reported by the `ps` command than they have in the Finder.  If so, check this box.

* **Process name**  
If the applications has a different name in `ps`, list it here.

#### States

* **Process Status**  
The current status of the process.  Values are 'Idle', 'Running', 'Stopped', 'Waiting', 'Zombie', or 'Off'.

* **Process ID**  
The process id.

* **Last Start**  
Timestamp of the last time the process was started.

* **Elapsed Seconds**  
Elapsed running time as an integer in seconds.

* **Elapsed Time**  
Elapsed running time as a string in format DD-HH:SS.

* **Percent CPU**  
The percent of CPU capacity the process is using as a float.

* **Percent Memory**  
The percent of memory the process is using as a float.

### Helper

This is for helper applications *without* a **.app** extension, *not* in the `/Applications` folder, and probably faceless.

#### Configuration

* **Helper name**  
The name of the name (including extension if any) of the helper application.

* **Directory path**  
The full path to the directory containing the helper application.


#### States

Same as above.

### Daemon

Uses `launchctl` command to add and remove jobs to `launchd`.  If the daemon was launched by another process, quitting may fail.

#### Configuration

* **Daemon process name**  
The name of the name (including extension if any) of the daemon.

* **Directory path**  
The full path to the directory containing the daemon.

* **Start command arguments**  
Optional arguments used when launching daemon.

#### States

Same as above.

### System Load

This device reports the total CPU and Memory utilization of the Indigo Server machine.

#### Configuration

No configuration, but you must 'SAVE' the cofiguration dialog!

#### States

* **Percent CPU**  
The percent of CPU capacity as a float.

* **Percent Memory**  
The percent of memory as a float.

* **Display State**  
String combining percent CPU and percent memory.
