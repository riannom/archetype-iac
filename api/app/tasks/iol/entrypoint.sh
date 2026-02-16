#!/bin/bash
set -e

IOL_PID="${IOL_PID:-1}"
IOL_BIN="/iol/iol.bin"
DATA_DIR="/iol/data"
CONFIG_DIR="/iol/configs"

mkdir -p "$DATA_DIR"

# -------------------------------------------------------------------------
# 1. Discover data interfaces (ethN, N>=1; eth0 is management)
# -------------------------------------------------------------------------
DATA_IFACES=()
for iface_path in /sys/class/net/eth*; do
    iface=$(basename "$iface_path")
    [ "$iface" = "eth0" ] && continue
    DATA_IFACES+=("$iface")
done

# Sort interfaces numerically
IFS=$'\n' DATA_IFACES=($(printf '%s\n' "${DATA_IFACES[@]}" | sort -t 'h' -k2 -n)); unset IFS

NUM_IFACES=${#DATA_IFACES[@]}

# Calculate number of slot groups (4 ports per slot)
NUM_SLOTS=$(( (NUM_IFACES + 3) / 4 ))
[ "$NUM_SLOTS" -lt 1 ] && NUM_SLOTS=1

# -------------------------------------------------------------------------
# 2. Generate iouyap.ini — maps IOL socket IDs to real ethX interfaces
# -------------------------------------------------------------------------
if [ "$NUM_IFACES" -gt 0 ]; then
    IOUYAP_INI="$DATA_DIR/iouyap.ini"
    echo "[default]" > "$IOUYAP_INI"
    echo "base_port = 49000" >> "$IOUYAP_INI"
    echo "netmap = $DATA_DIR/NETMAP" >> "$IOUYAP_INI"
    echo "" >> "$IOUYAP_INI"

    for i in "${!DATA_IFACES[@]}"; do
        slot=$(( i / 4 ))
        port=$(( i % 4 ))
        echo "[${IOL_PID}:${slot}/${port}]" >> "$IOUYAP_INI"
        echo "eth_dev = ${DATA_IFACES[$i]}" >> "$IOUYAP_INI"
        echo "" >> "$IOUYAP_INI"
    done
fi

# -------------------------------------------------------------------------
# 3. Generate NETMAP — defines IOL-to-IOUYAP topology
#    IOL instance uses ID=$IOL_PID, IOUYAP bridge uses ID=513
# -------------------------------------------------------------------------
if [ "$NUM_IFACES" -gt 0 ]; then
    NETMAP="$DATA_DIR/NETMAP"
    > "$NETMAP"
    for i in "${!DATA_IFACES[@]}"; do
        slot=$(( i / 4 ))
        port=$(( i % 4 ))
        echo "${IOL_PID}:${slot}/${port} 513:${slot}/${port}" >> "$NETMAP"
    done
fi

# -------------------------------------------------------------------------
# 4. Handle startup configuration
#    Priority: 1) /iol/data/config.txt (already placed)
#              2) /iol/configs/startup-config (bind-mounted by agent)
#              3) Generate minimal default
# -------------------------------------------------------------------------
if [ ! -f "$DATA_DIR/config.txt" ]; then
    if [ -f "$CONFIG_DIR/startup-config" ]; then
        cp "$CONFIG_DIR/startup-config" "$DATA_DIR/config.txt"
    else
        NODE_NAME="${HOSTNAME:-IOL}"
        cat > "$DATA_DIR/config.txt" <<EOFCFG
hostname $NODE_NAME
!
no ip domain lookup
ip ssh version 2
!
username admin privilege 15 password 0 admin
!
line con 0
 exec-timeout 0 0
line vty 0 15
 login local
 transport input ssh
!
interface Ethernet0/0
 no shutdown
!
end
EOFCFG
    fi
fi

# -------------------------------------------------------------------------
# 5. Flush eth0 addresses — IOL takes ownership of management interface
# -------------------------------------------------------------------------
ip addr flush dev eth0 2>/dev/null || true

# -------------------------------------------------------------------------
# 6. Start IOUYAP bridge (if data interfaces exist)
# -------------------------------------------------------------------------
if [ "$NUM_IFACES" -gt 0 ]; then
    /usr/bin/iouyap -q 513 -c "$IOUYAP_INI" &
    IOUYAP_PID=$!
    # Brief wait to let IOUYAP bind its sockets
    sleep 1
    if ! kill -0 "$IOUYAP_PID" 2>/dev/null; then
        echo "ERROR: IOUYAP failed to start" >&2
        exit 1
    fi
fi

# -------------------------------------------------------------------------
# 7. Start IOL inside a named screen session for console access
# -------------------------------------------------------------------------
cd "$DATA_DIR"

screen -dmS iol "$IOL_BIN" "$IOL_PID" \
    -e "$NUM_SLOTS" \
    -s 0 \
    -c "$DATA_DIR/config.txt" \
    -n 1024

# Wait for the screen session / IOL process to exit
while screen -list | grep -q "\.iol"; do
    sleep 2
done

echo "IOL process exited"
