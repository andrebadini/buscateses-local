; Inno Setup 6 script for BuscaTeses (per-user install, no administrator).
;
; AppId fixo (nunca altere, senão atualizações viram outra instalação):
;   cbafab11-c1fd-4624-934f-c742f4aa5b81
;
; The build script passes /DAppVersion, /DSourceDir, /DOutputDir and
; /DIconFile. Defaults below allow a manual compile.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\BuscaTeses"
#endif
#ifndef OutputDir
  #define OutputDir "..\output"
#endif
#ifndef IconFile
  #define IconFile "..\icons\icon.ico"
#endif

[Setup]
AppId={{cbafab11-c1fd-4624-934f-c742f4aa5b81}
AppName=BuscaTeses
AppVersion={#AppVersion}
AppVerName=BuscaTeses {#AppVersion}
AppPublisher=BuscaTeses contributors
AppPublisherURL=https://github.com/andrebadini/buscateses-local
AppSupportURL=https://github.com/andrebadini/buscateses-local/issues
AppUpdatesURL=https://github.com/andrebadini/buscateses-local/releases
DefaultDirName={localappdata}\Programs\BuscaTeses
DefaultGroupName=BuscaTeses
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=BuscaTeses-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\BuscaTeses.exe
UninstallDisplayName=BuscaTeses {#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BuscaTeses"; Filename: "{app}\BuscaTeses.exe"; WorkingDir: "{app}"
Name: "{group}\Desinstalar o BuscaTeses"; Filename: "{uninstallexe}"
Name: "{autodesktop}\BuscaTeses"; Filename: "{app}\BuscaTeses.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BuscaTeses.exe"; Description: "{cm:LaunchProgram,BuscaTeses}"; Flags: nowait postinstall skipifsilent

[Code]
var
  SmartScreenPage: TWizardPage;

procedure InitializeWizard;
var
  Info: TNewStaticText;
begin
  SmartScreenPage := CreateCustomPage(
    wpReady,
    'Aviso do Windows',
    'Como abrir o BuscaTeses pela primeira vez'
  );
  Info := TNewStaticText.Create(SmartScreenPage);
  Info.Parent := SmartScreenPage.Surface;
  Info.Left := 0;
  Info.Top := 0;
  Info.Width := SmartScreenPage.SurfaceWidth;
  Info.Height := 84;
  Info.WordWrap := True;
  Info.AutoSize := False;
  Info.Caption :=
    'Na primeira abertura, o Windows pode exibir a mensagem "O Windows protegeu o computador". ' +
    'Isso acontece porque o instalador não possui uma assinatura digital comercial. ' +
    'Clique em "Mais informações" e depois em "Executar assim mesmo". ' +
    'O BuscaTeses é um projeto de código aberto e não envia dados para a internet.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  { User data is preserved unless the user explicitly asks to delete it. }
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\BuscaTeses');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Deseja também apagar os dados do BuscaTeses?' + #13#10 + #13#10 +
        'Isso remove o catálogo baixado, o modelo, a biblioteca e as configurações em:' + #13#10 +
        DataDir + #13#10 + #13#10 +
        'Escolha "Não" para manter tudo e poder reinstalar depois.',
        mbConfirmation,
        MB_YESNO or MB_DEFBUTTON2
      ) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
