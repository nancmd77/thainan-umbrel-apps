# 9: diagnostico para automacao gpu (leitura)
C=/home/umbrel/umbrel/app-data/immich/docker-compose.yml
echo "== immich compose gpu"; grep -nE 'container_name|image:|gpus|driver|nvidia|capabilities|runtime|devices' $C | head -40
echo "== docker"; which docker; docker ps -a --format '{{.Names}} {{.Status}}' | grep -i immich
echo "== qemu pid/inicio"; P=$(pgrep -f 'guest=umbrel-machine-windows-11' | head -1); echo $P; ps -o lstart=,etime= -p $P
echo "== drv"; lspci -nnk -s 01:00 | grep -E 'driver'
echo "== hooks"; ls -la /etc/libvirt/hooks 2>&1; mount | grep -E ' / | /etc ' | head
echo "== systemd persist?"; ls /etc/systemd/system | head -30
