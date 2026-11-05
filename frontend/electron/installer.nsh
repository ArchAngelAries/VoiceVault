; VoiceVault additions to electron-builder's per-user NSIS installer.

; The app executable is not resource-edited (that needs extra signing tooling), so point the
; shortcuts at the bundled VoiceVault icon instead.
!macro customInstall
  ${if} ${FileExists} "$newStartMenuLink"
    CreateShortCut "$newStartMenuLink" "$appExe" "" "$INSTDIR\resources\icon.ico" 0 "" "" "${APP_DESCRIPTION}"
    ClearErrors
    WinShell::SetLnkAUMI "$newStartMenuLink" "${APP_ID}"
  ${endIf}
  ${if} ${FileExists} "$newDesktopLink"
    CreateShortCut "$newDesktopLink" "$appExe" "" "$INSTDIR\resources\icon.ico" 0 "" "" "${APP_DESCRIPTION}"
    ClearErrors
    WinShell::SetLnkAUMI "$newDesktopLink" "${APP_ID}"
  ${endIf}
!macroend

; Library, settings, models and the Python engine live in %LOCALAPPDATA%\VoiceVault.
; They are kept unless the user explicitly says Yes. Updates and silent uninstalls never remove them.
!macro customUnInstall
  ${ifNot} ${isUpdated}
  ${andIfNot} ${Silent}
    SetShellVarContext current
    ${if} ${FileExists} "$LOCALAPPDATA\VoiceVault\*.*"
      MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete your VoiceVault data?$\r$\n$\r$\nThis permanently removes your library, chats, settings, downloaded models and VoiceVault's Python engine from:$\r$\n$LOCALAPPDATA\VoiceVault$\r$\n$\r$\nChoose No to keep them (recommended if you plan to reinstall)." IDNO +2
      RMDir /r "$LOCALAPPDATA\VoiceVault"
    ${endIf}
  ${endIf}
!macroend
