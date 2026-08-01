#!/bin/zsh
# Switch the phone to Wi-Fi adb so the cable (and Mac battery drain) isn't needed.
# Usage: plug the phone in via USB once, then run ./wifi_adb.sh and unplug.
# Rerun after a phone reboot or Wi-Fi change.

PORT=5555

usb_serial=$(adb devices | awk 'NR>1 && $2=="device" && $1 !~ /:/ {print $1; exit}')
unauthorized=$(adb devices | awk 'NR>1 && $2=="unauthorized" {print $1; exit}')

if [ -n "$unauthorized" ]; then
  echo "Device $unauthorized is unauthorized — accept the USB debugging prompt on the phone, then rerun."
  exit 1
fi

if [ -z "$usb_serial" ]; then
  echo "No USB device found. Plug the phone in via cable, then rerun."
  exit 1
fi

echo "USB device: $usb_serial"
adb -s "$usb_serial" tcpip "$PORT"
sleep 2

ip=$(adb -s "$usb_serial" shell ip route 2>/dev/null | awk '/wlan/ {print $9; exit}')
if [ -z "$ip" ]; then
  echo "Could not read the phone's Wi-Fi IP. Is the phone connected to Wi-Fi?"
  exit 1
fi

echo "Phone Wi-Fi IP: $ip"
adb connect "$ip:$PORT"
echo ""
echo "Connected devices:"
adb devices
echo ""
echo "You can unplug the cable now. Wi-Fi adb target: $ip:$PORT"
