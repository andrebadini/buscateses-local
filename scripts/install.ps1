# Instala o BuscaTeses a partir do código-fonte no Windows.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 [-Local] [-Connect <cliente>] [-Yes]
[CmdletBinding()]
param(
    [switch]$Local,
    [string]$Connect = "",
    [switch]$Yes,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$Repository = "git+https://github.com/andrebadini/buscateses-local"

if ($Help) {
    Write-Host "Uso: install.ps1 [-Local] [-Connect <cliente>] [-Yes]"
    Write-Host "  -Local             instala a partir deste diretório (desenvolvimento)"
    Write-Host "  -Connect <cliente> conecta o assistente de IA indicado (ex.: claude_desktop)"
    Write-Host "  -Yes               não faz perguntas"
    exit 0
}

function Add-ToolPaths {
    $paths = @(
        (Join-Path $env:USERPROFILE ".local\bin"),
        (Join-Path $env:USERPROFILE ".cargo\bin")
    )
    foreach ($path in $paths) {
        if ($env:Path -notlike "*$path*") {
            $env:Path = "$path;$env:Path"
        }
    }
}

try {
    Add-ToolPaths
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "O uv não foi encontrado. Instalando o uv (gerenciador oficial)…"
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Add-ToolPaths
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "O uv foi instalado, mas não está no PATH. Abra um novo terminal e tente de novo."
    }

    if ($Local) {
        $localDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
        $specification = "$($localDirectory)[desktop]"
        Write-Host "Instalando o BuscaTeses a partir de $localDirectory…"
    }
    else {
        $specification = "buscateses-local[desktop] @ $Repository"
        Write-Host "Instalando o BuscaTeses a partir do repositório oficial…"
    }

    & uv tool install --python 3.12 --force $specification
    if ($LASTEXITCODE -ne 0) {
        throw "não foi possível instalar o BuscaTeses com o uv."
    }

    $toolBin = (& uv tool dir --bin).Trim()
    if ($toolBin) {
        $env:Path = "$toolBin;$env:Path"
    }
    Add-ToolPaths
    if (-not (Get-Command buscateses -ErrorAction SilentlyContinue)) {
        throw "o comando 'buscateses' não foi encontrado após a instalação."
    }

    Write-Host "Baixando o modelo de busca e o catálogo (pode levar alguns minutos)…"
    & buscateses setup --yes
    if ($LASTEXITCODE -ne 0) {
        throw "a preparação inicial falhou. Execute 'buscateses setup' para tentar de novo."
    }

    if (-not $Connect -and -not $Yes) {
        $answer = Read-Host "Deseja conectar o BuscaTeses a um assistente de IA agora? [s/N]"
        if ($answer -match '^(s|sim|y|yes)$') {
            & buscateses connect --list
            $Connect = Read-Host "Identificador do cliente (ex.: claude_desktop)"
        }
    }

    if ($Connect) {
        & buscateses connect $Connect
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Aviso: não foi possível conectar '$Connect'. Tente depois em 'buscateses connect'." -ForegroundColor Yellow
        }
    }

    Write-Host ""
    Write-Host "Tudo pronto! Abra com:"
    Write-Host "  buscateses"
    Write-Host ""
    Write-Host "Dica para assistentes de IA: configure o servidor MCP com 'buscateses connect <cliente>'."
}
catch {
    Write-Host "Erro: $_" -ForegroundColor Red
    exit 1
}
