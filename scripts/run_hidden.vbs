' run_hidden.vbs - 任意の .bat を完全非表示で実行する汎用ラッパー
'
' 使い方:
'   wscript.exe C:\path\to\run_hidden.vbs "C:\path\to\target.bat"
'
' 効果:
'   - cmd.exe のコンソール窓を表示しない (0 = SW_HIDE)
'   - 非同期で起動 (False) → VBS は即終了
'   - ユーザーが PC で作業中でも黒窓が前面に出てこない
'   - >> "%LOG%" 2>&1 のリダイレクトは中の .bat で機能する

If WScript.Arguments.Count < 1 Then
    WScript.Quit 1
End If

Set WshShell = CreateObject("WScript.Shell")
' "0" は SW_HIDE (完全に非表示), "False" は非同期実行
WshShell.Run """" & WScript.Arguments(0) & """", 0, False
