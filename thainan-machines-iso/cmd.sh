# 8: copiar iso para pasta da VM e trocar CD
D=/home/umbrel/umbrel/app-data/thainan-machines-iso/data
M=/run/umbrel-machines/windows-11/storage/media
ls -la $M; df -h $M | tail -1
cp $D/virtio-win.iso $M/virtio-win.iso && chown --reference=$M/install.iso $M/virtio-win.iso; chmod 644 $M/virtio-win.iso
virsh -c qemu:///system change-media umbrel-machine-windows-11 sdb $M/virtio-win.iso --live 2>&1
virsh -c qemu:///system domblklist umbrel-machine-windows-11 | grep sdb
