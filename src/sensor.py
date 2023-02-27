import re
import math
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
                    'temperature': value,
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

def getDisks(parseLimits=False, defaultLimits=[10.0, 60.0]):
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

    # Get disk temperatures from SMART data
    try:
        if parseLimits:
            smart_command = ['smartctl', '-x']
        else:
            smart_command = ['smartctl', '-A']

        rx_temp = [
            re.compile(r'^194 .* \s(\d*)\s?.*'),            # Try #1: Get temperature information from attribute #194
            re.compile(r'^190 .* \s(\d*)\s?.*'),            # Fallback -> Try #2: Get temperature information from attribute #190
            re.compile(r'[Tt]emperature.* \s(\d*)\s?.*')    # Fallback -> Try #3: Get temperature from attribute name (could be multiple -> first is chosen)
        ]
        rx_unit = [
            re.compile(r'.*(Celsius|Fahrenheit).*'),
            re.compile(r'.*(Cel|Fah).*')                    # Workaround for Samsung
        ]
        rx_max = re.compile(r'.*(Warning  Comp. Temp. Threshold|Specified Maximum Operating Temperature).*')
        rx_min = re.compile(r'.*(Specified Minimum Operating Temperature).*')
        rx_value = re.compile(r'.*\b(\d+)\b.*')

        # Parse output for each disk
        for disk in disks:
            smart_raw = subprocess.run(smart_command + ['/dev/' + disk], check=False, capture_output=True).stdout.decode('UTF-8')

            temp = float('NaN')
            unit = 'N/A'
            lnr = defaultLimits[0]
            lcr = defaultLimits[0]
            lnc = defaultLimits[0]
            unc = defaultLimits[1]
            ucr = defaultLimits[1]
            unr = defaultLimits[1]

            # Temperature data
            for rxt in rx_temp:
                for line in smart_raw.splitlines():
                    rmt = rxt.match(line)
                    if rmt is not None and len(rmt.groups()) > 0:
                        temp = float(rmt.group(1))
                        for rxu in rx_unit:
                            rmu = rxu.match(line)
                            if rmu is not None and len(rmu.groups()) > 0:
                                unit = rmu.group(1)
                                break
                        break
                if not math.isnan(temp):
                    break

            unit = re.sub(r"\b[Cc]el\b", "Celsius", unit)
            unit = re.sub(r"\b[Ff]ah\b", "Fahrenheit", unit)

            # Limits
            if parseLimits:
                for line in smart_raw.splitlines():
                    rm_max_line = rx_max.match(line)
                    if rm_max_line is not None and len(rm_max_line.groups()) > 0:
                        rm_max_value = rx_value.match(line)
                        if rm_max_value is not None and len(rm_max_value.groups()) > 0:
                            try:
                                unc = float(rm_max_value.group(1))
                                ucr = unc
                                unr = unc
                            except:
                                pass
                    else:
                        rm_min_line = rx_min.match(line)
                        if rm_min_line is not None and len(rm_min_line.groups()) > 0:
                            rm_min_value = rx_value.match(line)
                            if rm_min_value is not None and len(rm_min_value.groups()) > 0:
                                try:
                                    lnc = float(rm_min_value.group(1))
                                    lcr = lnc
                                    lnr = lnc
                                except:
                                    pass

            disks_data.append(
                {
                    'name': disk,
                    'temperature': temp,
                    'unit': unit,
                    'status': 'OK' if not math.isnan(temp) else 'FAIL',
                    'lnr': lnr,
                    'lcr': lcr,
                    'lnc': lnc,
                    'unc': unc,
                    'ucr': ucr,
                    'unr': unr
                }
            )
    except Exception as e:
        print('ERROR: {}'.format(e))

    return disks_data

if __name__ == '__main__':
    print('Zone1 Temperatures')
    sensors_1 = getIpmiTemps(['CPU'])
    for sensor in sensors_1:
        for key in sensor.keys():
            print(sensor[key], end='')
            if key != list(sensor.keys())[-1]:
                print(' ', end='')
        print()
    print()

    print('Zone2 Temperatures')
    sensors_2 = getIpmiTemps(['VRM', 'DIMM', 'NVMe', 'PCH', 'Peripheral', 'System'])
    for sensor in sensors_2:
        for key in sensor.keys():
            print(sensor[key], end='')
            if key != list(sensor.keys())[-1]:
                print(' ', end='')
        print()
    print()

    print('Disks')
    disks = getDisks()
    for disk in disks:
        print('/dev/', end='')
        for key in disk.keys():
            print(disk[key], end='')
            if key != list(disk.keys())[-1]:
                print(' ', end='')
        print()
