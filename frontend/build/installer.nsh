!include LogicLib.nsh
!define /ifndef INSTALL_REGISTRY_KEY "Software\${APP_GUID}"

!ifdef BUILD_UNINSTALLER
Function un.IsReparsePoint
  Exch $R0
  Push $R1
  System::Call 'kernel32::GetFileAttributesW(w r10)i.r11'
  IntOp $R1 $R1 & 0x400
  StrCpy $R0 "0"
  IntCmp $R1 0 done done isReparsePoint
  isReparsePoint:
    StrCpy $R0 "1"
  done:
    Pop $R1
    Exch $R0
FunctionEnd

Function un.ValidateInstallDirectory
  StrCpy $R0 "0"
  StrCpy $R9 "empty-path"
  StrCmp $INSTDIR "" done

  StrCpy $R9 "registry-path"
  ReadRegStr $R2 SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" InstallLocation
  StrCmp $R2 "" done
  StrCmp $INSTDIR $R2 0 done
  StrCpy $R7 $R2 1 1
  StrCmp $R7 ":" 0 done
  StrCpy $R7 $R2 1 2
  StrCmp $R7 "\" 0 done

  StrCpy $R9 "reparse-point"
  StrCpy $R6 $R2
  checkReparsePoint:
    Push $R6
    Call un.IsReparsePoint
    Pop $R5
    StrCmp $R5 "1" done
    StrLen $R8 $R6
    IntCmp $R8 3 pathHasNoReparsePoint pathHasNoReparsePoint findParentSeparator
    findParentSeparator:
      IntOp $R8 $R8 - 1
      StrCpy $R7 $R6 1 $R8
      StrCmp $R7 "\" parentFound findParentSeparator
    parentFound:
      IntCmp $R8 2 driveRoot parentWithoutSlash parentWithoutSlash
    driveRoot:
      IntOp $R8 $R8 + 1
    parentWithoutSlash:
      StrCpy $R6 $R6 $R8
    Goto checkReparsePoint

  pathHasNoReparsePoint:
  StrCpy $R9 "canonical-path"
  GetFullPathName $R1 "$INSTDIR"
  StrCmp $R1 "" done
  StrCpy $R9 "registry-path"
  GetFullPathName $R2 "$R2"
  StrCmp $R1 $R2 0 done

  StrCpy $R9 "protected-root"
  StrCpy $R3 $R1 3
  StrCmp $R1 $R3 done
  GetFullPathName $R4 "$PROGRAMFILES"
  StrCmp $R1 $R4 done
  GetFullPathName $R4 "$PROGRAMFILES64"
  StrCmp $R1 $R4 done
  GetFullPathName $R4 "$PROFILE"
  StrCmp $R1 $R4 done

  StrCpy $R0 "1"
  StrCpy $R9 "valid"
  done:
    Push $R0
FunctionEnd

Function un.LogRetainedInstallDirectory
  ClearErrors
  FileOpen $R8 "$TEMP\AI-Freelance-Studio-uninstall.log" a
  IfErrors done
  FileSeek $R8 0 END
  FileWrite $R8 "Install directory retained because it is not empty or did not pass safety checks (guard=$R9).$\r$\n"
  FileClose $R8
  done:
FunctionEnd

!macro customRemoveFiles
  Call un.ValidateInstallDirectory
  Pop $R0
  StrCmp $R0 "1" 0 fs_cleanup_unsafe

  Delete /REBOOTOK "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
  Delete /REBOOTOK "$INSTDIR\chrome_100_percent.pak"
  Delete /REBOOTOK "$INSTDIR\chrome_200_percent.pak"
  Delete /REBOOTOK "$INSTDIR\d3dcompiler_47.dll"
  Delete /REBOOTOK "$INSTDIR\ffmpeg.dll"
  Delete /REBOOTOK "$INSTDIR\icudtl.dat"
  Delete /REBOOTOK "$INSTDIR\libEGL.dll"
  Delete /REBOOTOK "$INSTDIR\libGLESv2.dll"
  Delete /REBOOTOK "$INSTDIR\LICENSE.electron.txt"
  Delete /REBOOTOK "$INSTDIR\LICENSES.chromium.html"
  Delete /REBOOTOK "$INSTDIR\resources.pak"
  Delete /REBOOTOK "$INSTDIR\snapshot_blob.bin"
  Delete /REBOOTOK "$INSTDIR\v8_context_snapshot.bin"
  Delete /REBOOTOK "$INSTDIR\vk_swiftshader_icd.json"
  Delete /REBOOTOK "$INSTDIR\vk_swiftshader.dll"
  Delete /REBOOTOK "$INSTDIR\vulkan-1.dll"
  Delete /REBOOTOK "$INSTDIR\uninstallerIcon.ico"
  Delete /REBOOTOK "$INSTDIR\${UNINSTALL_FILENAME}"
  RMDir /r "$INSTDIR\locales"
  RMDir /r "$INSTDIR\resources"

  SetOutPath "$TEMP"
  ClearErrors
  RMDir "$INSTDIR"
  IfErrors 0 fs_cleanup_done
  Call un.LogRetainedInstallDirectory
  Goto fs_cleanup_done

  fs_cleanup_unsafe:
    SetOutPath "$TEMP"
    Call un.LogRetainedInstallDirectory
  fs_cleanup_done:
!macroend
!endif
