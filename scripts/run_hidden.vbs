' run_hidden.vbs - Launch a .bat file completely hidden (no cmd window).
'
' ROOT-CAUSE NOTE (2026-05-16):
'   This file MUST be ASCII-only. wscript.exe on Japanese Windows
'   reads .vbs as CP932 (Shift-JIS); a UTF-8 file with Japanese
'   comments silently corrupts the parsed statements, and the
'   WshShell.Run call never actually launches cmd.exe.
'   See commit 14f0f6a (5/15) for the cmd.exe /c wrapping fix that
'   was necessary but NOT sufficient until this encoding fix.
'
' Usage:
'   wscript.exe C:\path\to\run_hidden.vbs "C:\path\to\target.bat"
'
' Effect:
'   - 0   = SW_HIDE     (no cmd window at all)
'   - False = async     (vbs returns immediately, bat runs detached)
'   - bat itself can redirect output with >> "%LOG%" 2>&1

If WScript.Arguments.Count < 1 Then
    WScript.Quit 1
End If

Set WshShell = CreateObject("WScript.Shell")
' cmd.exe /c is mandatory: wscript cannot launch .bat directly via
' file association in detached mode (it returns 0 but never runs).
WshShell.Run "cmd.exe /c """ & WScript.Arguments(0) & """", 0, False
