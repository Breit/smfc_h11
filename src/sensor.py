import re
import platform
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

            if rm is not None and len(rm.groups()) >= 10:
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
    disks = []
    disks_data = []

    # Get list of disks connected to the system
    if platform.system() == 'Linux':                        # TrueNAS SCALE / Linux
        command = ['fdisk', '-l']
        grep_disks = ['grep', '-e', 'Disk /dev/\w*:']

        # Actually get disk info
        try:
            # Run commands
            ps = subprocess.Popen(command, stdout=subprocess.PIPE)
            disks_raw = subprocess.run(grep_disks, stdin=ps.stdout, check=False, capture_output=True)

            # Parse output
            rx = re.compile(r'Disk \/dev\/(.*)\:')
            for disk in disks_raw.stdout.decode('UTF-8').splitlines():
                rm = rx.match(disk)

                if rm is not None and len(rm.groups()) > 0:
                    disks.append(rm.group(1).strip())

        except Exception as e:
            print('ERROR: {}'.format(e))
    elif platform.system() == 'FreeBSD':                    # TrueNAS CORE / FreeBSD
        command = ['sysctl', '-n', 'kern.disks']

        # Actually get disk info
        try:
            disks_raw = subprocess.run(command, check=False, capture_output=True)

            # Parse output
            for disk in disks_raw.stdout.decode('UTF-8').split():
                disks.append(disk)

        except Exception as e:
            print('ERROR: {}'.format(e))
    else:
        print('ERROR: Unsupported OS ({})'.format(platform.system()))

    # Get disk temperatures from SMART values
    try:
        smart_command = ['smartctl', '-A']
        for disk in disks:
            smart_raw = subprocess.run(smart_command + ['/dev/' + disk], check=False, capture_output=True).stdout.decode('UTF-8')

            # Get temperature from SMART data
            # Try #1: Get temperature information from attribute #194
            rx = re.compile(r'^194 .* \s(\d*)\s?.*')
            rm = None
            for line in smart_raw.splitlines():
                rm = rx.match(line)
                if rm is not None:
                    break

            # Fallback -> Try #2: Get temperature information from attribute #190
            if rm is None:
                rx = re.compile(r'^190 .* \s(\d*)\s?.*')
                for line in smart_raw.splitlines():
                    rm = rx.match(line)
                    if rm is not None:
                        break

            # Fallback -> Try #3: Get temperature from attribute name (could be multiple -> first is chosen)
            if rm is None:
                rx = re.compile(r'[Tt]emperature.* \s(\d*)\s?.*')
                for line in smart_raw.splitlines():
                    rm = rx.match(line)
                    if rm is not None:
                        break

            # Compile disk temperature data
            if rm is not None and len(rm.groups()) > 0:
                disks_data.append(
                    {
                        'name': disk,
                        'temperature': rm.group(1)
                    }
                )
    except Exception as e:
        print('ERROR: {}'.format(e))

    return disks_data

if __name__ == '__main__':
    print('Zone1 Temperatures')
    print(getIpmiTemps(['CPU', 'NVMe']))

    print('Zone2 Temperatures')
    print(getIpmiTemps(['VRM', 'DIMM']))

    print('Disks')
    print(getDisks())
