import re
import subprocess

def str2float(str)-> float:
    try:
        return (float(str))
    except:
        return float('NaN')

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
        rx = re.compile(r'(.*)[TtEeMmPp]{4}.*\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)')
        for line in sensors_raw.stdout.decode('UTF-8').splitlines():
            rm = rx.match(line)

            if len(rm.groups()) >= 10:
                name = rm.group(1).strip()
                value = str2float(rm.group(2).strip())
                unit = rm.group(3).strip()
                status = rm.group(4).strip()
                lnr = str2float(rm.group(5).strip())
                lcr = str2float(rm.group(6).strip())
                lnc = str2float(rm.group(7).strip())
                unc = str2float(rm.group(8).strip())
                ucr = str2float(rm.group(9).strip())
                unr = str2float(rm.group(10).strip())

                sensor_value = {
                    'name': name,
                    'value': value,
                    'unit': unit,
                    'status': status,
                    'lnr': lnr,
                    'lcr': lcr,
                    'lnc': lnc,
                    'unc': unc,
                    'ucr': ucr,
                    'unr': unr
                }

                sensor_values.append(sensor_value)

    except Exception as e:
        print('ERROR: {}'.format(e))

    return sensor_values

def getDisks():
    command = ['fdisk', '-l']
    grep_disks = ['grep', '-e', 'Disk /dev/\w*:']

    disks = []
    # Actually get sensor info
    try:
        # Run commands
        ps = subprocess.Popen(command, stdout=subprocess.PIPE)
        disks_raw = subprocess.run(grep_disks, stdin=ps.stdout, check=False, capture_output=True)

        # Parse output
        rx = re.compile(r'Disk \/dev\/(.*)\:')
        for line in disks_raw.stdout.decode('UTF-8').splitlines():
            rm = rx.match(line)

            if len(rm.groups()) > 0:
                disks.append(rm.group(1).strip())

    except Exception as e:
        print('ERROR: {}'.format(e))

    return disks

if __name__ == '__main__':

    print('Zone1 Temperatures')
    print(getIpmiTemps(['CPU', 'NVMe']))

    print('Zone2 Temperatures')
    print(getIpmiTemps(['VRM', 'DIMM']))

    print('Disks')
    print(getDisks())
