; Instalador do DocAudio (Inno Setup 6).
;
; Gere antes o executavel:  .venv\Scripts\pyinstaller.exe --noconfirm packaging\docaudio.spec
; Depois compile:            "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\docaudio.iss
; Saida:                     dist\instalador\DocAudio-<versao>-instalador.exe
;
; Instala so para o usuario atual (sem pedir administrador). Configuracoes, vozes
; baixadas e logs ficam em %LOCALAPPDATA%\DocAudio e nao sao apagados na desinstalacao.

#define AppName "DocAudio"
#define AppVersion "0.1.0"
#define AppExe "DocAudio.exe"

[Setup]
AppId={{8F3C2A61-5D7B-4E0A-9B1E-2C6D4F8A7B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\instalador
OutputBaseFilename={#AppName}-{#AppVersion}-instalador
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
SetupLogging=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
Name: "openwith"; Description: "Mostrar o DocAudio em ""Abrir com"" para DOCX, PDF, EPUB e PPTX"; GroupDescription: "Integração:"

[Files]
Source: "..\dist\DocAudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Abrir com" do Explorer: o arquivo vai para a fila (ou para a janela ja aberta).
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: openwith; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.docx\OpenWithList\{#AppExe}"; Tasks: openwith; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithList\{#AppExe}"; Tasks: openwith; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.epub\OpenWithList\{#AppExe}"; Tasks: openwith; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.pptx\OpenWithList\{#AppExe}"; Tasks: openwith; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent

[Messages]
ptbr.FinishedLabel=O DocAudio foi instalado.%n%nPara gerar MP3 ou M4B é preciso o FFmpeg (no Prompt de Comando: winget install Gyan.FFmpeg). Sem ele, o DocAudio gera WAV.
