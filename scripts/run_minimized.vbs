' run_minimized.vbs - Launch a .bat file in a MINIMIZED cmd window.
'
' Difference vs run_hidden.vbs:
'   run_hidden.vbs uses WshShell.Run "cmd /c ...", 0, False  (SW_HIDE)
'     -> No window appears at all (good for silent daily jobs)
'   run_minimized.vbs uses 7 = SW_SHOWMINNOACTIVE
'     -> A minimized cmd window appears in the taskbar without stealing focus.
'        User can see "boatrace job running" indicator at all times.
'
' Backlog item 1 (2026-05-18):
'   User wants 1-minute scheduler (odds_scheduler, beforeinfo_live) to be
'   visible (minimized) so they can confirm it is running on the taskbar.
'
' MUST be ASCII-only — wscript on Japanese Windows reads .vbs as CP932.
' UTF-8 with Japanese comments silently corrupts the parser and the call
' never runs. See commit 14f0f6a notes in run_hidden.vbs.
'
' Usage:
'   wscript.exe C:\path\to\run_minimized.vbs "C:\path\to\target.bat"

If WScript.Arguments.Count < 1 Then
    WScript.Quit 1
End If

Set WshShell = CreateObject("WScript.Shell")
' 7 = SW_SHOWMINNOACTIVE (minimized window, no focus steal)
' False = async (vbs returns immediately, bat runs detached)
WshShell.Run "cmd.exe /c """ & WScript.Arguments(0) & """", 7, False
