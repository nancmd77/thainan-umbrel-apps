# 5: troca audio pela gpu no slot livre
V="virsh -c qemu:///system"; D=umbrel-machine-windows-11
echo "== detach audio"; $V detach-device $D /run/gpu-vm/aud.xml --live 2>&1; sleep 4
echo "== attach gpu"; $V attach-device $D /run/gpu-vm/gpu2.xml --live 2>&1
sleep 3; $V dumpxml $D | grep -A6 "<hostdev" | grep -E "address"
