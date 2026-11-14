# 10: quais containers do immich reservam gpu (leitura)
for c in $(docker ps -a --format '{{.Names}}' | grep -i immich); do echo "$c $(docker inspect -f '{{json .HostConfig.DeviceRequests}} {{json .HostConfig.Runtime}} {{.State.Status}}' $c)"; done
ls /sys/bus/pci/devices/0000:01:00.0/driver -l; ls /sys/bus/pci/devices/0000:01:00.1/driver -l
virsh -c qemu:///system list --all
