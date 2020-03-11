#!/usr/bin/env python3
from fanshim import FanShim
from threading import Lock
import paho.mqtt.client as mqtt
import colorsys
import psutil
import argparse
import signal
import sys
from time import time, sleep, localtime, strftime

parser = argparse.ArgumentParser()
parser.add_argument('--threshold', type=float, default=-1, help='Temperature threshold in degrees C to enable fan')
parser.add_argument('--hysteresis', type=float, default=-1, help='Distance from threshold before fan is disabled')

parser.add_argument('--off-threshold', type=float, default=55.0, help='Temperature threshold in degrees C to enable fan')
parser.add_argument('--on-threshold', type=float, default=65.0, help='Temperature threshold in degrees C to disable fan')
parser.add_argument('--delay', type=float, default=2.0, help='Delay, in seconds, between temperature readings')
parser.add_argument('--preempt', action='store_true', default=False, help='Monitor CPU frequency and activate cooling premptively')
parser.add_argument('--verbose', action='store_true', default=False, help='Output temp and fan status messages')
parser.add_argument('--nobutton', action='store_true', default=False, help='Disable button input')
parser.add_argument('--noled', action='store_true', default=False, help='Disable LED control')
parser.add_argument('--brightness', type=float, default=255.0, help='LED brightness, from 0 to 255')
parser.add_argument('--mqttUser', default=None, help='User for authentication for mqtt broker')
parser.add_argument('--mqttPassword', default=None, help='Password for authentication for mqtt broker')
parser.add_argument('--mqttHost', default='localhost', help='host for mqtt broker')
parser.add_argument('--mqttPort', type=int, default=1883, help='port for mqtt broker')
parser.add_argument('--mqttKeepAlive', type=int, default=60, help='Keep alive for mqtt broker')

args = parser.parse_args()


def clean_exit(signum, frame):
    set_fan(False)
    if not args.noled:
        fanshim.set_light(0, 0, 0)
    sys.exit(0)


def update_led_temperature(temp):
    led_busy.acquire()
    temp = float(temp)
    temp -= args.off_threshold
    temp /= float(args.on_threshold - args.off_threshold)
    temp = max(0, min(1, temp))
    temp = 1.0 - temp
    temp *= 120.0
    temp /= 360.0
    r, g, b = [int(c * 255.0) for c in colorsys.hsv_to_rgb(temp, 1.0, args.brightness / 255.0)]
    fanshim.set_light(r, g, b)
    led_busy.release()


def get_cpu_temp():
    t = psutil.sensors_temperatures()
    for x in ['cpu-thermal', 'cpu_thermal']:
        if x in t:
            return t[x][0].current
    print("Warning: Unable to get CPU temperature!")
    return 0


def get_cpu_freq():
    freq = psutil.cpu_freq()
    return freq


def set_fan(status):
    global enabled
    changed = False
    if status != enabled:
        changed = True
        fanshim.set_fan(status)
    enabled = status
    return changed


def set_automatic(status):
    global armed, last_change
    armed = status
    last_change = 0

# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print('MQTT connection established')
        print()
    else:
        print('Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)))
        #kill main thread
        sys.exit(1)

def on_publish(client, userdata, mid):
    # print('Data successfully published.')
    pass

def publish(topic, value):
    print('Publish data {} - {}'.format(topic, value))
    mqtt_client.publish(topic, value)

if args.threshold > -1 or args.hysteresis > -1:
    print("""
The --threshold and --hysteresis parameters have been deprecated.
Use --on-threshold and --off-threshold instead!
""")
    sys.exit(1)

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

if args.mqttUser:
    mqtt_client.username_pw_set(args.mqttUser, args.mqttPassword)
try:
    mqtt_client.connect(args.mqttHost,
                        args.mqttPort,
                        args.mqttKeepAlive)
except:
    print('MQTT connection error. Please check your settings in the configuration file "config.ini"')
    sys.exit(1)
else:
    mqtt_client.loop_start()
    sleep(1.0) # some slack to establish the connection

fanshim = FanShim()
fanshim.set_hold_time(1.0)
fanshim.set_fan(False)
armed = True
enabled = False
led_busy = Lock()
enable = False
is_fast = False
last_change = 0
signal.signal(signal.SIGTERM, clean_exit)

if args.noled:
    led_busy.acquire()
    fanshim.set_light(0, 0, 0)
    led_busy.release()

t = get_cpu_temp()
if t >= args.threshold:
    last_change = get_cpu_temp()
    set_fan(True)


if not args.nobutton:
    @fanshim.on_release()
    def release_handler(was_held):
        global armed
        if was_held:
            set_automatic(not armed)
        elif not armed:
            set_fan(not enabled)

    @fanshim.on_hold()
    def held_handler():
        global led_busy
        if args.noled:
            return
        led_busy.acquire()
        for _ in range(3):
            fanshim.set_light(0, 0, 255)
            time.sleep(0.04)
            fanshim.set_light(0, 0, 0)
            time.sleep(0.04)
        led_busy.release()


try:
    while True:
        t = get_cpu_temp()
        publish('fanshim/temperature', t)
        f = get_cpu_freq()
        was_fast = is_fast
        is_fast = (int(f.current) == int(f.max))
        if args.verbose:
            print("Current: {:05.02f} Target: {:05.02f} Freq {: 5.02f} Automatic: {} On: {}".format(t, args.off_threshold, f.current / 1000.0, armed, enabled))

        if args.preempt and is_fast and was_fast:
            enable = True
        elif armed:
            if t >= args.on_threshold:
                enable = True
            elif t <= args.off_threshold:
                enable = False

        publish('fanshim/active', enable)
        if set_fan(enable):
            last_change = t

        if not args.noled:
            update_led_temperature(t)

        sleep(args.delay)
except KeyboardInterrupt:
    pass
