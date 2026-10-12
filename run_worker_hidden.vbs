Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "C:\자료\02_VSCode_Automation\01_kidsnote_bot\run_worker.bat" & chr(34), 0
Set WshShell = Nothing
