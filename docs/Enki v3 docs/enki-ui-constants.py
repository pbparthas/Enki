# Enki v3 UI Constants
# Referenced by orchestrator output, hook scripts, and CLI

# ANSI escape codes
GOLD = "\033[38;5;214m"
BOLD_WHITE = "\033[1;37m"
RESET = "\033[0m"

# The Enki Identity Prefix — used for all orchestrator output
ENKI_ICON = "𒀭"
ENKI_PREFIX = f"{GOLD}{ENKI_ICON} {BOLD_WHITE}Enki:{RESET}"

# Agent prefixes — no icon, just role name
AGENT_PREFIX = "{BOLD_WHITE}{role}:{RESET}"


def enki_print(message: str) -> None:
    """Print a message with the Enki identity prefix."""
    print(f"{ENKI_PREFIX} {message}")


def agent_print(role: str, message: str) -> None:
    """Print a message with an agent role prefix (no Enki icon)."""
    prefix = f"{BOLD_WHITE}{role}:{RESET}"
    print(f"  {prefix} {message}")


# Shell equivalents for hooks:
#
# ENKI_COLOR='\033[38;5;214m'
# BOLD_WHITE='\033[1;37m'
# NC='\033[0m'
# echo -e "${ENKI_COLOR}𒀭 ${BOLD_WHITE}Enki:${NC} Message here"
