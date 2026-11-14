# 6: baixar virtio-win no host (segundo plano)
D=/home/umbrel/umbrel/app-data/thainan-machines-iso/data
which curl wget python3 2>&1 | head
cd $D && rm -f virtio-win.iso.part
U=https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso
if command -v curl >/dev/null; then setsid sh -c "curl -sL -o virtio-win.iso.part $U && mv virtio-win.iso.part virtio-win.iso" >/dev/null 2>&1 < /dev/null &
else setsid sh -c "wget -q -O virtio-win.iso.part $U && mv virtio-win.iso.part virtio-win.iso" >/dev/null 2>&1 < /dev/null & fi
sleep 30; ls -la $D | grep virtio
