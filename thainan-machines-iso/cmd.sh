# 4: attach gpu sem rom
V="virsh -c qemu:///system"; D=umbrel-machine-windows-11
cat /run/gpu-vm/rtx2050.rom; echo
cat > /run/gpu-vm/gpu2.xml <<X
<hostdev mode='subsystem' type='pci' managed='no'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source></hostdev>
X
echo "== attach gpu"; $V attach-device $D /run/gpu-vm/gpu2.xml --live 2>&1
sleep 3; $V dumpxml $D | grep -A6 "<hostdev" | grep -E "address|hostdev mode"
