#!/bin/sh
# RTX 2050 segue a VM windows-11: VM ligada -> GPU na VM; VM desligada -> GPU volta pro host/Immich.
V="virsh -c qemu:///system"; D=umbrel-machine-windows-11
S=/run/gpu-vm; mkdir -p $S
log(){ echo "$(date '+%F %T') $*"; }
drv(){ basename "$(readlink /sys/bus/pci/devices/$1/driver 2>/dev/null)" 2>/dev/null; }
printf "<hostdev mode='subsystem' type='pci' managed='no'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source></hostdev>\n" > $S/gpu2.xml
log "gpu-vm: vigiando $D"
while :; do
  st=$($V domstate $D 2>/dev/null)
  if [ "$st" = "running" ]; then
    if ! $V dumpxml $D 2>/dev/null | grep -q "bus='0x01' slot='0x00' function='0x0'"; then
      log "VM ligada sem GPU: assumindo a RTX"
      for c in $(docker ps --format '{{.Names}}'); do
        if docker inspect "$c" 2>/dev/null | grep -qi nvidia; then echo "$c" >> $S/parados; log "parando $c"; docker stop -t 10 "$c" >/dev/null 2>&1; fi
      done
      [ "$(drv 0000:01:00.1)" = vfio-pci ] || $V nodedev-detach pci_0000_01_00_1 2>&1
      [ "$(drv 0000:01:00.0)" = vfio-pci ] || $V nodedev-detach pci_0000_01_00_0 2>&1
      if $V attach-device $D $S/gpu2.xml --live 2>&1; then
        P=$(pgrep -f "guest=$D" | head -1); et=$(ps -o etimes= -p "$P" 2>/dev/null | tr -d ' ')
        if [ -n "$et" ] && [ "$et" -lt 90 ]; then log "VM recem ligada (${et}s): reset para alocar a GPU"; $V reset $D 2>&1; else log "VM ligada ha ${et}s: GPU anexada; reinicie o Windows por dentro para ativar"; fi
      fi
    fi
  elif [ -z "$st" ] || [ "$st" = "shut off" ]; then
    if [ "$(drv 0000:01:00.0)" = vfio-pci ]; then
      log "VM desligada: devolvendo a RTX ao host"
      sleep 2
      $V nodedev-reattach pci_0000_01_00_0 2>&1; $V nodedev-reattach pci_0000_01_00_1 2>&1
      if [ -f $S/parados ]; then for c in $(sort -u $S/parados); do log "subindo $c"; docker start "$c" >/dev/null 2>&1; done; rm -f $S/parados; fi
    fi
  fi
  sleep 3
done
