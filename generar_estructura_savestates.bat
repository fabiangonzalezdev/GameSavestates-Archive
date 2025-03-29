@echo off
setlocal enabledelayedexpansion

:: Carpeta raíz del proyecto
set "root=SAVES"

:: Fabricantes y sus consolas
set "Nintendo=NES SNES N64 GameCube Wii WiiU Switch GameBoy GBC GBA DS 3DS VirtualBoy"
set "SEGA=SG-1000 MasterSystem Genesis MegaDrive SegaCD 32X Saturn Dreamcast GameGear"
set "Sony=PS1 PS2 PS3 PSP PSVita PS4 PS5"
set "Microsoft=Xbox Xbox360 XboxOne XboxSeriesX"
set "PC=Windows DOS Linux MacOS"
set "Others=NeoGeo Atari2600 Atari7800 AtariJaguar Wonderswan TurboGrafx-16 Amiga"

:: Crear carpeta raíz
md "%root%"
md "%root%\Tools"

:: README principal
> "%root%\README.md" (
    echo # 🎮 SAVE STATE ARCHIVE
    echo.
    echo Welcome to the ultimate save state archive for emulators across all major platforms.
    echo This archive is organized by manufacturer, console, emulator, and game.
    echo.
    echo ## Manufacturers included:
    echo - Nintendo
    echo - SEGA
    echo - Sony (PlayStation)
    echo - Microsoft (Xbox)
    echo - PC (Windows, Linux, macOS)
    echo - Others (retro/obscure consoles)
    echo.
    echo ## 📜 License
    echo Licensed under Creative Commons BY-NC-SA 4.0
)

:: README para Tools
> "%root%\Tools\README.md" (
    echo # 🧰 Tools
    echo Recommended emulator versions, configurations, and BIOS info will be added here.
)

:: Función para generar carpetas por fabricante y consola
call :crear_fabricante "Nintendo" %Nintendo%
call :crear_fabricante "SEGA" %SEGA%
call :crear_fabricante "Sony" %Sony%
call :crear_fabricante "Microsoft" %Microsoft%
call :crear_fabricante "PC" %PC%
call :crear_fabricante "Others" %Others%

echo ✅ All folders created successfully under "%root%"!
pause
exit /b

:crear_fabricante
set "fabricante=%~1"
shift
:loop
if "%~1"=="" goto :eof
    set "consola=%~1"
    set "ruta=%root%\%fabricante%\%consola%\EmulatorName\Game Name"
    md "!ruta!"

    :: README de ejemplo en cada juego
    > "!ruta!\README.md" (
        echo # 🎮 Save State: Game Name
        echo.
        echo **Platform:** %consola%
        echo **Manufacturer:** %fabricante%
        echo **Emulator:** EmulatorName vX.X.X
        echo.
        echo ## 📝 Description
        echo - Example: "Before final boss, all upgrades unlocked."
        echo.
        echo ## ⚙️ Requirements
        echo - Emulator settings
        echo - BIOS files (if needed)
        echo.
        echo ## 💡 Notes
        echo - Compatible with save file: .sav / .state / .bin
    )
shift
goto loop
