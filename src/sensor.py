import re
import subprocess

def getIpmiTemps(sensors: list):
    # Command to get all sensor info from IPMI
    ipmi_command = ['ipmitool', 'sensor']

    # Filter for temperatures
    grep_temperatures = ['grep', '-ie', 'temp']

    # Filter for requested sensors
    grep_sensors = ['grep']
    for s in sensors:
        grep_sensors.append('-ie')
        grep_sensors.append(s.lower())

    sensor_values = []
    # Actually get sensor info
    try:
        # Run commands
        ps_ipmi = subprocess.Popen(ipmi_command, stdout=subprocess.PIPE)
        ps_grep = subprocess.Popen(grep_temperatures, stdin=ps_ipmi.stdout, stdout=subprocess.PIPE)
        sensors_raw = subprocess.run(grep_sensors, stdin=ps_grep.stdout, check=False, capture_output=True)

        # Parse output
        # TODO: RegEx - r'(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)'
        sensor_values = sensors_raw.stdout.decode('UTF-8')
    except Exception as e:
        print('ERROR: {}'.format(e))

    return sensor_values

if __name__ == '__main__':

    print('Zone1 Temperatures')
    print(getIpmiTemps(['CPU']))

    print('Zone2 Temperatures')
    print(getIpmiTemps(['VRM', 'DIMM']))
