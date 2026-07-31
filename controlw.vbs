' SIGNAL LAB — silent launcher (fallback; shortcut prefers pythonw.exe directly)
Option Explicit
Dim sh, fso, root, pyw, script, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "D:\python\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
script = root & "\tools\control.py"
sh.CurrentDirectory = root
cmd = """" & pyw & """ """ & script & """"
sh.Run cmd, 0, False
