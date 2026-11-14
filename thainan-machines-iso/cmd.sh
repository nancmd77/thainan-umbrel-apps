# 2: RTX -> VM windows-11 (hot-plug)
set -u
V="virsh -c qemu:///system"; D=umbrel-machine-windows-11
mkdir -p /run/gpu-vm && cd /run/gpu-vm
echo 1 > /sys/bus/pci/devices/0000:01:00.0/rom; cat /sys/bus/pci/devices/0000:01:00.0/rom > rtx2050.rom; echo 0 > /sys/bus/pci/devices/0000:01:00.0/rom
ls -la rtx2050.rom; head -c 2 rtx2050.rom | od -An -tx1
chmod 644 rtx2050.rom; chown libvirt-qemu:libvirt-qemu rtx2050.rom 2>/dev/null
cat > gpu.xml <<X
<hostdev mode='subsystem' type='pci' managed='yes'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source><rom file='/run/gpu-vm/rtx2050.rom'/></hostdev>
X
cat > aud.xml <<X
<hostdev mode='subsystem' type='pci' managed='yes'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/></source></hostdev>
X
echo "== attach gpu"; $V attach-device $D gpu.xml --live 2>&1
echo "== attach audio"; $V attach-device $D aud.xml --live 2>&1
sleep 3
lspci -nnk -s 01:00 | grep -E "^0|driver"
$V dumpxml $D | grep -A4 "<hostdev" | head -20
tail -5 /run/umbrel-machines/windows-11/qemu.log
