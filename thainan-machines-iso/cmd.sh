# 7: trocar CD da VM pelo virtio-win
D=/home/umbrel/umbrel/app-data/thainan-machines-iso/data
ls -la $D | grep virtio
virsh -c qemu:///system change-media umbrel-machine-windows-11 sdb $D/virtio-win.iso --live 2>&1
virsh -c qemu:///system domblklist umbrel-machine-windows-11
