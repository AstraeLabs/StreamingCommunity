#!/usr/bin/env bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0;5m' # No Color
NC_BOLD='\033[1m'
NC_REG='\033[0m'

echo -e "${BLUE}====================================================${NC_REG}"
echo -e "${GREEN}      VibraVid Android/Termux Autoinstaller         ${NC_REG}"
echo -e "${BLUE}====================================================${NC_REG}"

# 1. Check if running in Termux
if [ -z "$TERMUX_VERSION" ] && [ ! -d "/data/data/com.termux/files/usr" ]; then
    echo -e "${RED}Error: Questo script deve essere eseguito all'interno di Termux su Android!${NC_REG}"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# Force dpkg to use default actions for config file prompts and never ask questions
mkdir -p "$PREFIX/etc/apt/apt.conf.d"
echo 'Dpkg::Options { "--force-confdef"; "--force-confold"; };' > "$PREFIX/etc/apt/apt.conf.d/99force-conf"

# Helper function to clean up our custom apt config
cleanup_apt_config() {
    rm -f "$PREFIX/etc/apt/apt.conf.d/99force-conf"
}
# Set trap to run cleanup on script exit (successful or error)
trap cleanup_apt_config EXIT

# Check if we need to clone the repository (in case run via curl)
if [ ! -f "setup.py" ] && [ ! -f "pyproject.toml" ]; then
    echo -e "${YELLOW}VibraVid non rilevato nella cartella corrente. Preparazione installazione...${NC_REG}"
    
    # Ensure git is installed to perform the clone
    if ! command -v git &> /dev/null; then
        echo -e "${BLUE}Installazione di Git...${NC_REG}"
        pkg update -y < /dev/null && pkg install -y git < /dev/null || {
            echo -e "${RED}Impossibile installare Git!${NC_REG}"
            exit 1
        }
    fi
    
    if [ -d "StreamingCommunity" ]; then
        echo -e "${BLUE}La cartella StreamingCommunity esiste già. Aggiorno all'ultima versione...${NC_REG}"
        cd StreamingCommunity || exit 1
        git pull < /dev/null || {
            echo -e "${RED}Errore durante il git pull!${NC_REG}"
            exit 1
        }
    else
        echo -e "${BLUE}Clonazione del repository da ManoloZocco/StreamingCommunity...${NC_REG}"
        git clone https://github.com/ManoloZocco/StreamingCommunity.git < /dev/null || {
            echo -e "${RED}Errore durante il clone del repository!${NC_REG}"
            exit 1
        }
        cd StreamingCommunity || exit 1
    fi
fi

VIBRAVID_DIR=$(pwd)

# 2. Storage permission setup
echo -e "\n${YELLOW}[1/5] Verifica permessi di archiviazione...${NC_REG}"
if [ ! -d "$HOME/storage" ]; then
    echo -e "${BLUE}Richiesta permessi di archiviazione Android. Controlla il popup a schermo...${NC_REG}"
    termux-setup-storage < /dev/null
    echo -e "${YELLOW}Premi INVIO dopo aver concesso i permessi per continuare...${NC_REG}"
    read -r < /dev/tty
fi

# Ensure /sdcard/Movies exists
mkdir -p /sdcard/Movies/VibraVid
echo -e "${GREEN}Cartella di destinazione creata: /sdcard/Movies/VibraVid${NC_REG}"

# Create Video shortcut
if [ ! -e "$HOME/Video" ]; then
    ln -s /sdcard/Movies/VibraVid "$HOME/Video"
    echo -e "${GREEN}Collegamento ~/Video creato verso la memoria condivisa.${NC_REG}"
fi

# 3. Package Updates
echo -e "\n${YELLOW}[2/5] Aggiornamento dei repository di Termux...${NC_REG}"
pkg update -y < /dev/null

# 4. Install system packages and repositories
echo -e "\n${YELLOW}[3/5] Installazione delle dipendenze di sistema...${NC_REG}"
# Enable X11 repo for mkvtoolnix
pkg install -y x11-repo < /dev/null
pkg install -y python ffmpeg mkvtoolnix rust clang git cmake make < /dev/null || {
    echo -e "${RED}Errore durante l'installazione dei pacchetti di sistema!${NC_REG}"
    exit 1
}

# 4b. Compile Bento4 (mp4decrypt and mp4dump) from source
echo -e "\n${YELLOW}[3b/5] Compilazione di Bento4 (mp4decrypt/mp4dump) da sorgente...${NC_REG}"
if [ -f "$HOME/.local/bin/binary/mp4decrypt" ] && [ -f "$HOME/.local/bin/binary/mp4dump" ]; then
    echo -e "${GREEN}Bento4 (mp4decrypt/mp4dump) è già compilato e presente.${NC_REG}"
else
    echo -e "${BLUE}Clonazione e compilazione di Bento4 (axiomatic-systems/Bento4)...${NC_REG}"
    git clone https://github.com/axiomatic-systems/Bento4.git "$HOME/Bento4_src" < /dev/null || {
        echo -e "${RED}Errore nel clonare Bento4!${NC_REG}"
        exit 1
    }
    cd "$HOME/Bento4_src" || exit 1
    mkdir cmakebuild
    cd cmakebuild || exit 1
    cmake -DCMAKE_BUILD_TYPE=Release .. < /dev/null
    make -j$(nproc 2>/dev/null || echo 2) < /dev/null || {
        echo -e "${RED}Errore durante la compilazione di Bento4!${NC_REG}"
        exit 1
    }
    mkdir -p "$HOME/.local/bin/binary"
    cp mp4decrypt mp4dump "$HOME/.local/bin/binary/"
    chmod +x "$HOME/.local/bin/binary/mp4decrypt" "$HOME/.local/bin/binary/mp4dump"
    cd "$HOME" || exit 1
    rm -rf "$HOME/Bento4_src"
    echo -e "${GREEN}Bento4 compilato con successo!${NC_REG}"
fi

# 5. Compile Velora
echo -e "\n${YELLOW}[4/5] Installazione e compilazione di Velora...${NC_REG}"
mkdir -p "$HOME/.local/bin/binary"
if [ -f "$HOME/.local/bin/binary/velora" ]; then
    echo -e "${GREEN}Velora è già installato in local binary directory.${NC_REG}"
else
    echo -e "${BLUE}Compilazione di Velora da sorgente tramite Cargo (potrebbe richiedere qualche minuto)...${NC_REG}"
    cargo install --quiet --git https://github.com/AstraeLabs/Velora --root "$HOME/.local" < /dev/null || {
        echo -e "${RED}Errore durante la compilazione di Velora!${NC_REG}"
        exit 1
    }
    
    if [ -f "$HOME/.local/bin/Velora" ]; then
        mv "$HOME/.local/bin/Velora" "$HOME/.local/bin/binary/velora"
    elif [ -f "$HOME/.local/bin/velora" ]; then
        mv "$HOME/.local/bin/velora" "$HOME/.local/bin/binary/velora"
    fi
    chmod +x "$HOME/.local/bin/binary/velora"
    echo -e "${GREEN}Velora compilato ed installato correttamente in ~/.local/bin/binary/velora${NC_REG}"
fi

# 5b. Compile dovi_tool
echo -e "\n${YELLOW}[4b/5] Installazione e compilazione di dovi_tool...${NC_REG}"
if [ -f "$HOME/.local/bin/binary/dovi_tool" ]; then
    echo -e "${GREEN}dovi_tool è già installato in local binary directory.${NC_REG}"
else
    echo -e "${BLUE}Compilazione di dovi_tool da sorgente tramite Cargo (potrebbe richiedere qualche minuto)...${NC_REG}"
    cargo install --quiet --git https://github.com/quietvoid/dovi_tool --root "$HOME/.local" < /dev/null || {
        echo -e "${RED}Errore durante la compilazione di dovi_tool!${NC_REG}"
        exit 1
    }
    
    if [ -f "$HOME/.local/bin/dovi_tool" ]; then
        mv "$HOME/.local/bin/dovi_tool" "$HOME/.local/bin/binary/dovi_tool"
    fi
    chmod +x "$HOME/.local/bin/binary/dovi_tool"
    echo -e "${GREEN}dovi_tool compilato ed installato correttamente in ~/.local/bin/binary/dovi_tool${NC_REG}"
fi

# 6. Install VibraVid Python Package
echo -e "\n${YELLOW}[5/5] Installazione del pacchetto Python VibraVid...${NC_REG}"
cd "$VIBRAVID_DIR" || exit 1

# Set Android API Level to prevent cryptography compilation errors
export ANDROID_API_LEVEL=24

# Upgrade core python packages
pip install --upgrade pip setuptools wheel < /dev/null

pip install . < /dev/null

# Create lowercase symlink for command availability
usr_bin="/data/data/com.termux/files/usr/bin"
if [ -f "$usr_bin/VibraVid" ]; then
    ln -sf "$usr_bin/VibraVid" "$usr_bin/vibravid"
    echo -e "${GREEN}Collegamento simbolico 'vibravid' (minuscolo) creato in $usr_bin${NC_REG}"
fi

echo -e "\n${GREEN}====================================================${NC_REG}"
echo -e "${GREEN}      Installazione completata con successo!        ${NC_REG}"
echo -e "${GREEN}====================================================${NC_REG}"
echo -e "Ora puoi avviare l'applicazione scrivendo semplicemente:"
echo -e "${BLUE}  vibravid${NC_REG}"
echo -e "I video verranno salvati in: ${BLUE}/sdcard/Movies/VibraVid/${NC_REG}"
