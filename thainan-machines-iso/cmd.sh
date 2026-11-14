# 3: detach ambos, depois attach managed=no
set -u
V="virsh -c qemu:///system"; D=umbrel-machine-windows-11
cd /run/gpu-vm
echo "== grupo 16"; ls /sys/kernel/iommu_groups/16/devices/
echo "== detach"; $V nodedev-detach pci_0000_01_00_1 2>&1; $V nodedev-detach pci_0000_01_00_0 2>&1
lspci -nnk -s 01:00 | grep driver
echo 1 > /sys/bus/pci/devices/0000:01:00.0/rom; cat /sys/bus/pci/devices/0000:01:00.0/rom > rtx2050.rom 2>&1; echo 0 > /sys/bus/pci/devices/0000:01:00.0/rom
ls -la rtx2050.rom; R=""; [ -s rtx2050.rom ] && R="<rom file='/run/gpu-vm/rtx2050.rom'/>"
chmod 644 rtx2050.rom
cat > gpu.xml <<X
<hostdev mode='subsystem' type='pci' managed='no'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source>$R</hostdev>
X
sed -i "s/managed='yes'/managed='no'/" aud.xml
echo "== attach gpu"; $V attach-device $D gpu.xml --live 2>&1
echo "== attach audio"; $V attach-device $D aud.xml --live 2>&1
sleep 3; lspci -nnk -s 01:00 | grep driver
$V dumpxml $D | grep -A3 "<hostdev" | head -12
