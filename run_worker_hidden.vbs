Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
batPath = scriptDir & "\run_worker.bat"

' Right after boot, the antivirus real-time protection driver can still be
' re-initializing, which briefly blocks file access and makes WshShell.Run
' fail with "file not found". Retry until the file becomes reachable.
For i = 1 To 6
    If FSO.FileExists(batPath) Then
        WshShell.Run chr(34) & batPath & chr(34), 0
        Exit For
    End If
    WScript.Sleep 5000
Next

Set FSO = Nothing
Set WshShell = Nothing
