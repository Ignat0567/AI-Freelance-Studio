Unicode true
Name "AIFS Sandbox Fixture"

!ifndef OUTPUT_FILE
  !define OUTPUT_FILE "${__FILEDIR__}\build\aifs-sandbox-fixture-v1.exe"
!endif

OutFile "${OUTPUT_FILE}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\AIFS Sandbox Fixture"
SetCompress off
CRCCheck on
ShowInstDetails nevershow

Section "Fixture" SEC_FIXTURE
  SetOutPath "$INSTDIR"
  File "/oname=fixture-manifest.json" "${__FILEDIR__}\fixture-manifest.json"
  File "/oname=payload.txt" "${__FILEDIR__}\payload.txt"
  SetErrorLevel 0
SectionEnd
