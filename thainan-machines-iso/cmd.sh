# 1: leitura (sem mudancas)
echo "== quem usa /dev/nvidia*"; for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q /dev/nvidia && echo "$(basename $p) $(tr '\0' ' ' <$p/cmdline | cut -c1-90)"; done | sort -u -k2 | head -30
echo "== servicos nvidia"; systemctl list-units --no-legend | grep -iE 'nvidia|persist' 
echo "== apps com gpu"; grep -lE 'nvidia|gpus|driver: nvidia' /home/umbrel/umbrel/app-data/*/docker-compose.yml 2>/dev/null
echo "== root ports livres na VM"; virsh -c qemu:///system dumpxml umbrel-machine-windows-11 | grep -cE "pcie-root-port"; virsh -c qemu:///system dumpxml umbrel-machine-windows-11 | grep -E "<address type='pci'" | sed 's/.*bus=//' | sort | uniq -c
echo "== iommu cmdline"; cat /proc/cmdline
echo "== rom"; ls -la /sys/bus/pci/devices/0000:01:00.0/rom; lspci -nnk -s 01:00 2>&1
echo "== libvirt versao"; virsh -c qemu:///system version 2>&1 | head -4
echo "== domain gerado"; ls -la /run/umbrel-machines/windows-11/ 2>&1 | head
