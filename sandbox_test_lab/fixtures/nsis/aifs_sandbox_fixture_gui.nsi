Unicode true
Name "AIFS Sandbox Fixture"
Caption "AIFS Sandbox Fixture"

!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "WinMessages.nsh"

!ifndef GUI_OUTPUT_FILE
  !define GUI_OUTPUT_FILE "${__FILEDIR__}\build\AIFS Sandbox Fixture.exe"
!endif

OutFile "${GUI_OUTPUT_FILE}"
RequestExecutionLevel user
SetCompress off
CRCCheck on
ShowInstDetails nevershow

Page custom ShowFixtureWindow

Function ShowFixtureWindow
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  GetDlgItem $1 $HWNDPARENT 1
  ShowWindow $1 ${SW_HIDE}
  GetDlgItem $1 $HWNDPARENT 2
  SendMessage $1 ${WM_SETTEXT} 0 "STR:Close"
  ${NSD_CreateLabel} 24u 36u 250u 24u "Controlled Windows Sandbox GUI fixture"
  Pop $1
  nsDialogs::Show
FunctionEnd

Section
SectionEnd
