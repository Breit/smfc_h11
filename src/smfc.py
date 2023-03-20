#!/usr/bin/python3
#
#   smfc.py (C) 2020-2022, Peter Sulyok
#   IPMI fan controller for Super Micro X9/X10/X11 motherboards.
#
#   Modified for Supermicro H11SSL-* by Martin Breitbarth, 2023
#
#   This is based on version 2.3.1 of smfc.py and modded to work
#   on Supermicro boards like the H11SSL-i, but not exclusively on that board.
#
#   The main reason for the fork is flexibility: It is no longer necessary to
#   specify the sensors and disks which are used to control the fan speed.
#   All is gathered dynamically based on some user-definable specs (regarding
#   IPMI sensors) and all disks found on the system. Even if during runtime
#   new disks/sensors should become available, they are picked up and used to
#   control the fans.
#   Further, each sensor has it's own limits taken from the IPMI directly or
#   specified based on disk type (HDD/SSD) for disk sensors, respectively.
#
import argparse
import configparser
import platform
import re
import subprocess
import sys
import syslog
import time
import math
from typing import List, Callable, Union

# Program version string
version_str: str = '0.1.0'

def str2float(str)-> float:
    try:
        return (float(str))
    except:
        return float('NaN')


class Log:
    """
    Log class. This class can send log messages considering different log levels and different outputs
    """

    # Configuration parameters.
    log_level: int                      # Log level
    log_output: int                     # Log output
    msg: Callable[[int, str], None]     # Function reference to the log function (based on log output)

    # Constants for log levels.
    LOG_NONE: int = 0
    LOG_ERROR: int = 1
    LOG_INFO: int = 2
    LOG_DEBUG: int = 3

    # Constants for log outputs.
    LOG_STDOUT: int = 0
    LOG_STDERR: int = 1
    LOG_SYSLOG: int = 2

    def __init__(self, log_level: int, log_output: int) -> None:
        """
        Initialize Log class with log output and log level.

        Args:
            log_level (int): user defined log level (LOG_NONE, LOG_ERROR, LOG_INFO, LOG_DEBUG)
            log_output (int): user defined log output (LOG_STDOUT, LOG_STDERR, LOG_SYSLOG)
        """
        # Setup log configuration.
        if log_level not in {self.LOG_NONE, self.LOG_ERROR, self.LOG_INFO, self.LOG_DEBUG}:
            raise ValueError(f'Invalid log level value ({log_level})')
        self.log_level = log_level
        if log_output not in {self.LOG_STDOUT, self.LOG_STDERR, self.LOG_SYSLOG}:
            raise ValueError(f'Invalid log output value ({log_output})')
        self.log_output = log_output
        if self.log_output == self.LOG_STDOUT:
            self.msg = self.msg_to_stdout
        elif self.log_output == self.LOG_STDERR:
            self.msg = self.msg_to_stderr
        else:
            self.msg = self.msg_to_syslog
            syslog.openlog('smfc.service', facility=syslog.LOG_DAEMON)

        # Print the configuration out at DEBUG log level.
        if self.log_level >= self.LOG_DEBUG:
            self.msg(Log.LOG_DEBUG, 'Logging module was initialized with:')
            self.msg(Log.LOG_DEBUG, f'   log_level = {self.log_level}')
            self.msg(Log.LOG_DEBUG, f'   log_output = {self.log_output}')

    def map_to_syslog(self, level: int) -> int:
        """
        Map log level to syslog values.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
        Returns:
            int: syslog log level
        """
        syslog_level = syslog.LOG_ERR
        if level == self.LOG_INFO:
            syslog_level = syslog.LOG_INFO
        elif level == self.LOG_DEBUG:
            syslog_level = syslog.LOG_DEBUG
        return syslog_level

    def level_to_str(self, level: int) -> str:
        """
        Convert a log level to a string.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
        Returns:
            str: log level string
        """
        string = 'ERROR'
        if level == self.LOG_INFO:
            string = 'INFO'
        elif level == self.LOG_DEBUG:
            string = 'DEBUG'
        return string

    def msg_to_syslog(self, level: int, msg: str) -> None:
        """
        Print a log message to syslog.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str): log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                syslog.syslog(self.map_to_syslog(level), msg)

    def msg_to_stdout(self, level: int, msg: str) -> None:
        """
        Print a log message to stdout.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str):  log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                print(f'{self.level_to_str(level)}: {msg}', flush=True, file=sys.stdout)

    def msg_to_stderr(self, level: int, msg: str) -> None:
        """
        Print a log message to stderr.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str):  log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                print(f'{self.level_to_str(level)}: {msg}', flush=True, file=sys.stderr)


class Ipmi:
    """
    IPMI interface class. It can set/get modes of IPMI fan zones and can set IPMI fan levels using ipmitool.
    """

    log: Log                            # Reference to a Log class instance
    command: str                        # Full path for ipmitool command.
    fan_mode_delay: float               # Delay time after execution of IPMI set fan mode function
    fan_level_delay: float              # Delay time after execution of IPMI set fan level function
    swapped_zones: bool                 # CPU and HD zones are swapped
    impi_alternate_mode: bool           # Some Supermicro X9 boards like the X9DRi-F use a different
                                        # IPMI command to set fan speeds

    # Constant values for IPMI fan modes:
    STANDARD_MODE: int = 0
    FULL_MODE: int = 1
    OPTIMAL_MODE: int = 2
    HEAVY_IO_MODE: int = 4

    # Constant values for IPMI fan zones:
    CPU_ZONE: int = 0
    HD_ZONE: int = 1

    # Constant values for the results of IPMI operations:
    SUCCESS: int = 0
    ERROR: int = -1

    def __init__(self, log: Log, config: configparser.ConfigParser) -> None:
        """
        Initialize the Ipmi class with a log class and with a configuration class.

        Args:
            log (Log): Log class
            config (configparser.ConfigParser): configuration values
        """
        # Set default or read from configuration
        self.log = log
        self.command = config['Paths'].get('ipmitool_path', '/usr/bin/ipmitool')
        self.fan_mode_delay = config['Ipmi'].getint('fan_mode_delay', fallback=10)
        self.fan_level_delay = config['Ipmi'].getint('fan_level_delay', fallback=2)
        self.swapped_zones = config['Ipmi'].getboolean('swapped_zones', fallback=False)
        self.impi_alternate_mode = config['Ipmi'].getboolean('impi_alternate_mode', fallback=False)

        # Validate configuration
        # Check 1: a valid command can be executed successfully.
        try:
            subprocess.run([self.command, 'sdr'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as e:
            raise e
        # Check 2: fan_mode_delay must be positive.
        if self.fan_mode_delay < 0:
            raise ValueError(f'Negative fan_mode_delay ({self.fan_mode_delay})')
        # Check 3: fan_mode_delay must be positive.
        if self.fan_level_delay < 0:
            raise ValueError(f'Negative fan_level_delay ({self.fan_level_delay})')
        # Print the configuration out at DEBUG log level.
        if self.log.log_level >= self.log.LOG_DEBUG:
            self.log.msg(self.log.LOG_DEBUG, 'Ipmi module was initialized with :')
            self.log.msg(self.log.LOG_DEBUG, f'   command = {self.command}')
            self.log.msg(self.log.LOG_DEBUG, f'   fan_mode_delay = {self.fan_mode_delay}')
            self.log.msg(self.log.LOG_DEBUG, f'   fan_level_delay = {self.fan_level_delay}')
            self.log.msg(self.log.LOG_DEBUG, f'   swapped_zones = {self.swapped_zones}')

    def get_fan_mode(self) -> int:
        """
        Get the current IPMI fan mode.

        Returns:
            int: fan mode (ERROR, STANDARD_MODE, FULL_MODE, OPTIMAL_MODE, HEAVY_IO_MODE)

        Raises:
            FileNotFoundError: ipmitool command cannot be found
            ValueError: output of the ipmitool cannot be interpreted/converted
            RuntimeError: ipmitool execution problem in IPMI (e.g. non-root user, incompatible IPMI systems
                or motherboards)
        """
        r: subprocess.CompletedProcess  # result of the executed process
        m: int                          # fan mode

        # Read the current IPMI fan mode.
        try:
            r = subprocess.run([self.command, 'raw', '0x30', '0x45', '0x00'],
                               check=False, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr)
            m = int(r.stdout)
        except (FileNotFoundError, ValueError) as e:
            raise e
        return m

    def get_fan_mode_name(self, mode: int) -> str:
        """
        Get the name of the specified IPMI fan mode.

        Args:
            mode (int): fan mode
        Returns:
            str: name of the fan mode ('ERROR', 'STANDARD MODE', 'FULL MODE', 'OPTIMAL MODE', 'HEAVY IO MODE')
        """
        fan_mode_name: str              # Name of the fan mode

        fan_mode_name = 'ERROR'
        if mode == self.STANDARD_MODE:
            fan_mode_name = 'STANDARD_MODE'
        elif mode == self.FULL_MODE:
            fan_mode_name = 'FULL_MODE'
        elif mode == self.OPTIMAL_MODE:
            fan_mode_name = 'OPTIMAL_MODE'
        elif mode == self.HEAVY_IO_MODE:
            fan_mode_name = 'HEAVY IO MODE'
        return fan_mode_name

    def set_fan_mode(self, mode: int) -> None:
        """
        Set the IPMI fan mode.

        Args:
            mode (int): fan mode (STANDARD_MODE, FULL_MODE, OPTIMAL_MODE, HEAVY_IO_MODE)
        """
        # Validate mode parameter.
        if mode not in {self.STANDARD_MODE, self.FULL_MODE, self.OPTIMAL_MODE, self.HEAVY_IO_MODE}:
            raise ValueError(f'Invalid fan mode value ({mode}).')

        # Call ipmitool command and set the new IPMI fan mode.
        try:
            subprocess.run(
                [self.command, 'raw', '0x30', '0x45', '0x01', str(mode)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError as e:
            raise e

        # Give time for IPMI system/fans to apply changes in the new fan mode.
        time.sleep(self.fan_mode_delay)

    def set_fan_level(self, zone: int, level: int) -> None:
        """
        Set the IPMI fan level in a specific zone. Raise an exception in case of invalid parameters.

        Args:
            zone (int): fan zone (CPU_ZONE_TAG, HD_ZONE_TAG)
            level (int): fan level in % (0-100)
        """
        # Validate zone parameter
        if zone not in {self.CPU_ZONE, self.HD_ZONE}:
            raise ValueError(f'Invalid value: zone ({zone}).')

        # Handle swapped zones
        if self.swapped_zones:
            zone = 1 - zone

        # Validate level parameter (must be in the interval [0..100%])
        if level not in range(0, 101):
            raise ValueError(f'Invalid value: level ({level}).')

        # Set the new IPMI fan level in the specific zone
        try:
            if self.impi_alternate_mode:
                subprocess.run(
                    [self.command, 'raw', '0x30', '0x91', '0x5A', '0x03', str(hex(16 + zone)), str(hex((255 * level) / 100.0))],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            else:
                subprocess.run(
                    [self.command, 'raw', '0x30', '0x70', '0x66', '0x01', str(hex(zone)), str(hex(level))],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
        except FileNotFoundError as e:
            raise e

        # Give time for IPMI and fans to spin up/down.
        time.sleep(self.fan_level_delay)


class Sensor:
    """
    Sensor interface class.
    IPMI interface class.
    """
    name: str               # sensor name
    temperature: float      # temperature value
    unit: str               # temperature unit
    status: str             # IPMI sensor status
    lnr: float              # lower non-recoverable temperature
    lcr: float              # lower critical temperature
    lnc: float              # lower non-critical temperature
    unc: float              # upper non-critical temperature
    ucr: float              # upper critical temperature
    unr: float              # upper non-recoverable temperature
    type: str               # optional type

    def __init__(
        self, name: str, temperature: float, unit: str, status: str,
        lnr: float, lcr: float, lnc: float, unc: float, ucr: float, unr: float,
        type: str = None
    ) -> None:
        self.name = name
        self.temperature = temperature
        self.unit = unit
        self.status = status
        self.lnr = lnr
        self.lcr = lcr
        self.lnc = lnc
        self.unc = unc
        self.ucr = ucr
        self.unr = unr
        self.type = type

    @classmethod
    def getRelTemp(self) -> float:
        """
        Temperature relative to [lnc, unc] range.
        """
        l: float = min(max(self.temperature, self.unc), self.lnc) - self.lnc
        u: float = self.unc - self.lnc
        r: float = l / u
        return max(min(r, 1.0), 0.0) if r else 1.0

    @staticmethod
    def getIpmiTemps(ipmitool_path: str, sensor_spec: List[str], sensor_limits: Union[List[float], None] = None):
        """
        Get sensor list based on sensor_spec list from IPMI
        If sensor_limits is provided, it overwrites the IPMI limits
        """
        # Command to get all sensor info from IPMI
        ipmi_command = [ipmitool_path, 'sensor']

        # Filter for temperatures
        grep_temperatures = ['grep', '-ie', 'temp']

        # Filter for requested sensors
        grep_sensors = ['grep']
        for s in sensor_spec:
            grep_sensors.append('-ie')
            grep_sensors.append(s.lower())

        sensor_list: List[Sensor] = []

        # Actually get sensor info
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

                if sensor_limits and len(sensor_limits) == 2 and sensor_limits[0] < sensor_limits[1]:
                    if sensor_limits[0]:
                        lnc = sensor_limits[0]
                        lcr = lnc if lnc < lcr else lcr
                        lnr = lnc if lnc < lnr else lnr
                    if sensor_limits[1]:
                        unc = sensor_limits[1]
                        ucr = unc if unc < ucr else ucr
                        unr = unc if unc < unr else unr

                sensor_list.append(
                    Sensor(
                        name,
                        value,
                        unit,
                        status,
                        lnr,
                        lcr,
                        lnc,
                        unc,
                        ucr,
                        unr
                    )
                )

        return sensor_list

    # TODO: Differentiate limits between SSD and HDD
    @staticmethod
    def getDiskTemps(smartctl_path: str, parse_limits=False, limits_hdd=[10.0, 50.0], limits_ssd=[10.0, 70.0]):
        def getDisks_FreeBSD():
            try:
                disks = []
                command = ['sysctl', '-n', 'kern.geom.confxml']

                # Get disk info from GEOM
                geom_confxml_raw = subprocess.run(command, check=False, capture_output=True)
                geom_tree = ET.ElementTree(ET.fromstring(geom_confxml_raw.stdout.decode('UTF-8')))

                disk_tree = geom_tree.getroot().findall('.//class[name="DISK"]//provider')
                for disk in disk_tree:
                    disks.append(
                        {
                            'name': disk.find('.//name').text,
                            'type': 'SSD' if disk.find('.//config/rotationrate').text == '0' else 'HDD'
                        }
                    )
            except:     # Fallback to only report disk names (without type) if parsing GEOM XML goes wrong
                disks = []
                command = ['sysctl', '-n', 'kern.disks']

                # Actually get disk info
                disks_raw = subprocess.run(command, check=False, capture_output=True)

                # Parse command output
                for disk in disks_raw.stdout.decode('UTF-8').split():
                    disks.append(
                        {
                            'name': disk,
                            'type': 'Unknown'
                        }
                    )

            return disks

        def getDisks_Linux():
            disks = []

            command = ['lsblk', '-nido', 'KNAME,ROTA,MODEL']

            # Actually get disk info
            disks_raw = subprocess.run(command, check=False, capture_output=True)

            # Parse command output
            rx = re.compile(r'^(\w*)[ \t]\s+(\d)[ \t]\w.+')
            for disk in disks_raw.stdout.decode('UTF-8').splitlines():
                rm = rx.match(disk)

                if rm is not None and len(rm.groups()) > 1:
                    disks.append(
                        {
                            'name': rm.group(1).strip(),
                            'type': 'SSD' if rm.group(2).strip() == '0' else 'HDD'
                        }
                    )

            return disks

        # Get list of disks connected to the system
        if platform.system() == 'Linux':                        # TrueNAS SCALE / Linux
            disks = getDisks_Linux()
        elif platform.system() == 'FreeBSD':                    # TrueNAS CORE / FreeBSD
            disks = getDisks_FreeBSD()
        else:
            raise Exception('Unsupported OS ({})'.format(platform.system()))

        # Get disk temperatures from SMART data
        if parse_limits:
            smart_command = [smartctl_path, '-x']
        else:
            smart_command = [smartctl_path, '-A']

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

        disk_list = []

        # Parse output for each disk
        for disk in disks:
            smart_raw = subprocess.run(smart_command + ['/dev/' + disk['name']], check=False, capture_output=True).stdout.decode('UTF-8')

            temp = float('NaN')
            unit = 'N/A'
            if disk['type'] == 'SSD':
                lnr = limits_ssd[0]
                lcr = limits_ssd[0]
                lnc = limits_ssd[0]
                unc = limits_ssd[1]
                ucr = limits_ssd[1]
                unr = limits_ssd[1]
            else:
                lnr = limits_hdd[0]
                lcr = limits_hdd[0]
                lnc = limits_hdd[0]
                unc = limits_hdd[1]
                ucr = limits_hdd[1]
                unr = limits_hdd[1]

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
            if parse_limits:
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

            disk_list.append(
                Sensor(
                    disk['name'],
                    temp,
                    unit,
                    'FAIL' if math.isnan(temp) else 'OK',
                    lnr,
                    lcr,
                    lnc,
                    unc,
                    ucr,
                    unr,
                    disk['type']
                )
            )

        return disk_list


class FanController:
    """
    Generic fan controller class for an IPMI zone.
    """

    # Constant values for temperature calculation
    CALC_MIN: int = 0
    CALC_AVG: int = 1
    CALC_MAX: int = 2
    CALC_ONE: int = 3

    # Error messages.
    ERROR_MSG_FILE_IO: str = 'Cannot read file ({}).'

    # Configuration parameters
    log: Log                # Reference to a Log class instance
    ipmi: Ipmi              # Reference to an Ipmi class instance
    ipmi_zone: int          # IPMI zone identifier
    name: str               # Name of the controller
    temp_calc: int          # Calculate of the temperature (0-min, 1-avg, 2-max)
    steps: int              # Discrete steps in temperatures and fan levels
    sensitivity: float      # Temperature change to activate fan controller (C)
    polling: float          # Polling interval to read temperature (sec)
    min_level: int          # Minimum fan level (0..100%)
    max_level: int          # Maximum fan level (0..100%)
    sensors: List[Sensor]   # List of sensors

    # Measured or calculated attributes
    temp_step: float        # A temperature steps value (C)
    level_step: float       # A fan level step value (0..100%)
    last_time: float        # Last system time we polled temperature (timestamp)
    last_temp: float        # Last measured temperature value (C)
    last_level: int         # Last configured fan level (0..100%)

    # Function variable for selected temperature calculation method
    get_temp_func: Callable[[], float]

    def __init__(
        self, log: Log, ipmi: Ipmi, ipmi_zone: int, name: str, temp_calc: int, steps: int,
        sensitivity: float, polling: float, min_level: int, max_level: int) -> None:
        """
        Initialize the FanController class. Will raise an exception in case of invalid parameters.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            ipmi_zone (int): IPMI zone identifier
            name (str): name of the controller
            temp_calc (int): calculation of temperature
            steps (int): discrete steps in temperatures and fan levels
            sensitivity (float): temperature change to activate fan controller (C)
            polling (float): polling time interval for reading temperature (sec)
            min_level (int): minimum fan level value [0..100%]
            max_level (int): maximum fan level value [0..100%]
            sensors (List[Sensor]): list of sensors
        """
        # Save and validate configuration parameters.
        self.log = log
        self.ipmi = ipmi
        self.ipmi_zone = ipmi_zone
        if self.ipmi_zone not in {Ipmi.CPU_ZONE, Ipmi.HD_ZONE}:
            raise ValueError('invalid value: ipmi_zone')
        self.name = name
        self.temp_calc = temp_calc
        if self.temp_calc not in {self.CALC_MIN, self.CALC_AVG, self.CALC_MAX}:
            raise ValueError('invalid value: temp_calc')
        self.steps = steps
        if self.steps <= 0:
            raise ValueError('steps <= 0')
        self.sensitivity = sensitivity
        if self.sensitivity <= 0.0:
            raise ValueError('sensitivity <= 0.0')
        if self.sensitivity > 1.0:
            raise ValueError('sensitivity > 1.0')
        self.polling = polling
        if self.polling < 0:
            raise ValueError('polling < 0')
        if max_level < min_level:
            raise ValueError('max_level < min_level')
        self.min_level = min_level
        self.max_level = max_level

        # Set the proper temperature function.
        if self.temp_calc == self.CALC_MIN:
            self.get_temp_func = self.get_min_temp
        elif self.temp_calc == self.CALC_MAX:
            self.get_temp_func = self.get_max_temp
        elif self.temp_calc == self.CALC_AVG:
            self.get_temp_func = self.get_avg_temp
        elif self.temp_calc == self.CALC_ONE:
            self.get_temp_func = self.get_1_temp
        else:
            self.get_temp_func = self.get_avg_temp

        # Initialize calculated and measured values.
        self.temp_step = 1.0 / steps
        self.level_step = (max_level - min_level) / steps
        self.last_temp = 0
        self.last_level = 0
        self.last_time = time.monotonic() - (polling + 1)

        # Print configuration at DEBUG log level.
        if self.log.log_level >= self.log.LOG_DEBUG:
            self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller was initialized with:')
            self.log.msg(self.log.LOG_DEBUG, f'   IPMI zone = {self.ipmi_zone}')
            self.log.msg(self.log.LOG_DEBUG, f'   steps = {self.steps}')
            self.log.msg(self.log.LOG_DEBUG, f'   sensitivity = {self.sensitivity}')
            self.log.msg(self.log.LOG_DEBUG, f'   polling = {self.polling}')
            self.log.msg(self.log.LOG_DEBUG, f'   min_level = {self.min_level}')
            self.log.msg(self.log.LOG_DEBUG, f'   max_level = {self.max_level}')
            for sensor in self.sensors:
                self.log.msg(self.log.LOG_DEBUG, f'   sensor {sensor.name} = {sensor.temperature:.1f} ({sensor.getRelTemp():.3f})')

    def update_sensors(self) -> None:
        """
        Function template for updating sensor information.
        Needs to get overwritten by derived classes.
        """
        pass

    def get_1_temp(self) -> float:
        """
        Get a single temperature of the controlled entities in the zone.
        """
        value: float = float('NaN')

        try:
            self.update_sensors()
            if len(self.sensors) > 0:
                value = self.sensors[0].getRelTemp()
                self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller used relative temperature: FIRST = {(value * 100):.0f}% ({self.sensors[0].name}: {self.sensors[0].temperature:.1f} {self.sensors[0].unit})')
        except Exception as e:
            self.log.msg(self.log.LOG_ERROR, f'Error while reading temperature: {e}')

        return value

    def get_min_temp(self) -> float:
        """
        Get the minimum temperature of the controlled entities in the zone.
        """
        value: float = float('NaN')

        try:
            self.update_sensors()
            if len(self.sensors) > 0:
                name: str
                temp: float
                unit: str
                for sensor in self.sensors:
                    if math.isnan(value):
                        value = sensor.getRelTemp()
                        name = sensor.name
                        temp = sensor.temperature
                        unit = sensor.unit
                    else:
                        v: float = sensor.getRelTemp()
                        if v < value:
                            name = sensor.name
                            temp = sensor.temperature
                            unit = sensor.unit
                        value = v
                self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller used temperature: MINIMUM = {(value * 100):.0f}% ({name}: {temp:.1f} {unit})')
        except Exception as e:
            self.log.msg(self.log.LOG_ERROR, f'Error while reading temperature: {e}')

        return value

    def get_avg_temp(self):
        """
        Get average temperature of the controlled entities in the zone.
        """
        value: float = float('NaN')

        try:
            self.update_sensors()
            if len(self.sensors) > 0:
                cnt: int = 0
                for sensor in self.sensors:
                    v: float = sensor.getRelTemp()
                    if math.isnan(value):
                        value = v
                    elif not math.isnan(v):
                        value += v
                        cnt += 1
                if cnt > 0:
                    value = value / cnt
            self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller used temperature: AVERAGE = {(value * 100):.0f}%')
        except Exception as e:
            self.log.msg(self.log.LOG_ERROR, f'Error while reading temperature: {e}')

        return value

    def get_max_temp(self) -> float:
        """
        Get the maximum temperature of the controlled entities in the zone.
        """
        value: float = float('NaN')

        try:
            self.update_sensors()
            print('SensorCount:', len(self.sensors))
            for sensor in self.sensors:
                print(sensor.name, sensor.value)
            if len(self.sensors) > 0:
                name: str
                temp: float
                unit: str
                for sensor in self.sensors:
                    if math.isnan(value):
                        value = sensor.getRelTemp()
                        if (math.isnan(value)):
                            print('NaN')
                            continue
                        name = sensor.name
                        temp = sensor.temperature
                        unit = sensor.unit
                    else:
                        v: float = sensor.getRelTemp()
                        if v > value:
                            name = sensor.name
                            temp = sensor.temperature
                            unit = sensor.unit
                        value = v
                self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller used temperature: MAXIMUM = {(value * 100):.0f}% ({name}: {temp:.1f} {unit})')
        except Exception as e:
            self.log.msg(self.log.LOG_ERROR, f'Error while reading temperature: {e}')

        return value

    def set_fan_level(self, level: int) -> None:
        """
        Set the new fan level in an IPMI zone. Can raise exception (ValueError).

        Args:
            level (int): new fan level [0..100]
        Returns:
            int: result (Ipmi.SUCCESS, Ipmi.ERROR)
        """
        return self.ipmi.set_fan_level(self.ipmi_zone, level)

    def run(self) -> None:
        """
        Run IPMI zone controller function with the following steps:

        * Step 1: Read current time. If the elapsed time is bigger than the polling time period
                  then go to step 2, otherwise return.
        * Step 2: Read the current temperature. If the change of the temperature goes beyond
                  the sensitivity limit then go to step 3, otherwise return
        * Step 3: Calculate the current gain and fan level based on the measured temperature
        * Step 4: If the new fan level is different it will be set and logged
        """
        current_time: float     # Current system timestamp (measured)
        current_temp: float     # Current temperature (measured)
        current_level: int      # Current fan level (calculated)
        current_gain: int       # Current gain level (calculated)

        # Step 1: check the elapsed time.
        current_time = time.monotonic()
        if current_time - self.last_time < self.polling:
            return
        self.last_time = current_time

        # Step 2: read temperature and sensitivity gap.
        current_temp = self.get_temp_func()
        if (math.isnan(current_temp)):
            print(f'ERROR: current_temp = {current_temp}')
        if abs(current_temp - self.last_temp) < self.sensitivity:
            return
        self.last_temp = current_temp

        # Step 3: calculate gain and fan level.
        current_gain = int(round(current_temp / self.temp_step))
        current_level = int(round(float(current_gain) * self.level_step)) + self.min_level

        # Step 4: the new fan level will be set and logged.
        if current_level != self.last_level:
            self.last_level = current_level
            self.set_fan_level(current_level)
            self.log.msg(self.log.LOG_INFO, f'{self.name}: new fan level > {current_level}% ({current_temp:.3f})')

class CpuZone(FanController):
    """
    CPU zone fan control.
    """

    # CpuZone specific parameters
    CPU_ZONE_TAG: str = 'CPU zone'  # CPU zone chapter name in the configuration file.
    sensor_spec: List[str]
    ipmitool_path: str
    smartctl_path: str
    limits: List[float]

    def __init__(self, log: Log, ipmi: Ipmi, config: configparser.ConfigParser) -> None:
        """
        Initialize the CpuZone class and raise exception in case of invalid configuration.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            config (configparser.ConfigParser): reference to the configuration (default=None)
        """

        # Initialize sensors
        self.sensor_spec = config[self.CPU_ZONE_TAG].get('sensor_spec')
        self.ipmitool_path = config['Paths'].get('ipmitool_path', fallback='/usr/bin/ipmitool')
        self.limits = [
            config[self.CPU_ZONE_TAG].getfloat('min_temp', fallback=None),
            config[self.CPU_ZONE_TAG].getfloat('max_temp', fallback=None)
        ]

        # Initialize FanController class.
        super().__init__(
            log,
            ipmi,
            Ipmi.CPU_ZONE,
            self.CPU_ZONE_TAG,
            config[self.CPU_ZONE_TAG].getint('temp_calc', fallback=FanController.CALC_AVG),
            config[self.CPU_ZONE_TAG].getint('steps', fallback=6),
            config[self.CPU_ZONE_TAG].getfloat('sensitivity', fallback=0.05),
            config[self.CPU_ZONE_TAG].getfloat('polling', fallback=2),
            config[self.CPU_ZONE_TAG].getint('min_level', fallback=35),
            config[self.CPU_ZONE_TAG].getint('max_level', fallback=100)
        )

    def update_sensors(self) -> None:
        self.sensors = Sensor.getIpmiTemps(self.ipmitool_path, self.sensor_spec, self.limits)

class HdZone(FanController):
    """
    Class for HD zone fan control.
    """

    # HdZone specific parameters
    HD_ZONE_TAG: str = 'HD zone'        # HD zone chapter name in the configuration file.
    sensor_spec: List[str]
    ipmitool_path: str
    smartctl_path: str
    limits: List[float]
    limits_hdd: List[float]
    limits_ssd: List[float]
    parse_limits: bool

    def __init__(self, log: Log, ipmi: Ipmi, config: configparser.ConfigParser) -> None:
        """
        Initialize the HdZone class. Abort in case of configuration errors.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            config (configparser.ConfigParser): reference to the configuration (default=None)
        """

        # Initialize sensors
        self.sensor_spec = config[self.HD_ZONE_TAG].get('sensor_spec')
        self.ipmitool_path = config['Paths'].get('ipmitool_path', fallback='/usr/bin/ipmitool')
        self.smartctl_path = config['Paths'].get('smartctl_path', fallback='/usr/bin/smartctl')
        self.limits = [
            config[self.HD_ZONE_TAG].getfloat('min_temp', fallback=None),
            config[self.HD_ZONE_TAG].getfloat('max_temp', fallback=None)
        ]
        self.limits_hdd = [
            config[self.HD_ZONE_TAG].getfloat('min_temp_hdd', fallback=10.0),
            config[self.HD_ZONE_TAG].getfloat('max_temp_hdd', fallback=50.0)
        ]
        self.limits_ssd = [
            config[self.HD_ZONE_TAG].getfloat('min_temp_ssd', fallback=10.0),
            config[self.HD_ZONE_TAG].getfloat('max_temp_ssd', fallback=70.0)
        ]
        self.parse_limits = config[self.HD_ZONE_TAG].getboolean('parse_limits', fallback=False)

        # Initialize FanController class.
        super().__init__(
            log,
            ipmi,
            Ipmi.HD_ZONE,
            self.HD_ZONE_TAG,
            config[self.HD_ZONE_TAG].getint('temp_calc', fallback=FanController.CALC_AVG),
            config[self.HD_ZONE_TAG].getint('steps', fallback=4),
            config[self.HD_ZONE_TAG].getfloat('sensitivity', fallback=0.02),
            config[self.HD_ZONE_TAG].getfloat('polling', fallback=10),
            config[self.HD_ZONE_TAG].getint('min_level', fallback=35),
            config[self.HD_ZONE_TAG].getint('max_level', fallback=100)
        )

    def update_sensors(self) -> None:
        print('HD')
        self.sensors = \
            Sensor.getIpmiTemps(self.ipmitool_path, self.sensor_spec, self.limits) + \
            Sensor.getDiskTemps(self.smartctl_path, self.parse_limits, self.limits_hdd, self.limits_ssd)
        print(self.sensors.count())

def main():
    """
    Main function: starting point of the systemd service.
    """
    my_parser: argparse.ArgumentParser      # Instance for an ArgumentParser class
    my_results: argparse.Namespace          # Results of parsed command line arguments
    my_config: configparser.ConfigParser    # Instance for a parsed configuration
    my_log: Log                             # Instance for a Log class
    my_ipmi: Ipmi                           # Instance for an Ipmi class
    my_cpu_zone: CpuZone                    # Instance for a CPU Zone fan controller class
    my_hd_zone: HdZone                      # Instance for an HD Zone fan controller class
    old_mode: int                           # Old IPMI fan mode
    cpu_zone_enabled: bool                  # CPU zone fan controller enabled
    hd_zone_enabled: bool                   # HD zone fan controller enabled

    # Parse the command line arguments.
    my_parser = argparse.ArgumentParser()
    my_parser.add_argument(
        '-c',
        action='store',
        dest='config_file',
        default='smfc.conf',
        help='configuration file'
    )
    my_parser.add_argument(
        '-v',
        action='version',
        version='%(prog)s ' + version_str
    )
    my_parser.add_argument(
        '-l',
        type=int,
        choices=[0, 1, 2, 3],
        default=1,
        help='log level: 0-NONE, 1-ERROR(default), 2-INFO, 3-DEBUG'
    )
    my_parser.add_argument(
        '-o',
        type=int,
        choices=[0, 1, 2],
        default=2,
        help='log output: 0-stdout, 1-stderr, 2-syslog(default)'
    )
    my_results = my_parser.parse_args()

    # Create a Log class instance (in theory this cannot fail).
    try:
        my_log = Log(my_results.l, my_results.o)
    except ValueError as e:
        print(f'ERROR: {e}.', flush=True, file=sys.stdout)
        sys.exit(5)

    if my_log.log_level >= my_log.LOG_DEBUG:
        my_log.msg(my_log.LOG_DEBUG, 'Command line arguments:')
        my_log.msg(my_log.LOG_DEBUG, f'   original arguments: {" ".join(sys.argv[:])}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed config file = {my_results.config_file}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed log level = {my_results.l}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed log output = {my_results.o}')

    # Parse and load configuration file.
    my_config = configparser.ConfigParser()
    if not my_config or not my_config.read(my_results.config_file):
        my_log.msg(my_log.LOG_ERROR, f'Cannot load configuration file ({my_results.config_file})')
        sys.exit(6)
    my_log.msg(my_log.LOG_DEBUG, f'Configuration file ({my_results.config_file}) loaded')

    # Create an Ipmi class instances and set required IPMI fan mode.
    try:
        my_ipmi = Ipmi(my_log, my_config)
        old_mode = my_ipmi.get_fan_mode()
    except (ValueError, FileNotFoundError) as e:
        my_log.msg(my_log.LOG_ERROR, f'{e}.')
        sys.exit(7)
    my_log.msg(my_log.LOG_DEBUG, f'Old IPMI fan mode = {my_ipmi.get_fan_mode_name(old_mode)}')
    if old_mode != my_ipmi.FULL_MODE:
        my_ipmi.set_fan_mode(my_ipmi.FULL_MODE)
        my_log.msg(my_log.LOG_DEBUG, f'New IPMI fan mode = {my_ipmi.get_fan_mode_name(my_ipmi.FULL_MODE)}')

    # Create an instance for CPU zone fan controller if enabled.
    my_cpu_zone = None
    cpu_zone_enabled = my_config['CPU zone'].getboolean('enabled', fallback=False)
    if cpu_zone_enabled:
        my_log.msg(my_log.LOG_DEBUG, 'CPU zone fan controller enabled')
        my_cpu_zone = CpuZone(my_log, my_ipmi, my_config)

    # Create an instance for HD zone fan controller if enabled.
    my_hd_zone = None
    hd_zone_enabled = my_config['HD zone'].getboolean('enabled', fallback=False)
    if hd_zone_enabled:
        my_log.msg(my_log.LOG_DEBUG, 'HD zone fan controller enabled')
        my_hd_zone = HdZone(my_log, my_ipmi, my_config)

    # Calculate the default sleep time for the main loop.
    if cpu_zone_enabled and hd_zone_enabled:
        wait = min(my_cpu_zone.polling, my_hd_zone.polling) / 2
    elif cpu_zone_enabled and not hd_zone_enabled:
        wait = my_cpu_zone.polling / 2
    elif not cpu_zone_enabled and hd_zone_enabled:
        wait = my_hd_zone.polling / 2
    else:  # elif not cpu_zone_enabled and not hd_controller_enabled:
        my_log.msg(my_log.LOG_ERROR, 'None of the fan controllers are enabled, service terminated.')
        sys.exit(8)
    my_log.msg(my_log.LOG_DEBUG, f'Main loop wait time = {wait} sec')

    # Main execution loop.
    while True:
        if cpu_zone_enabled:
            my_cpu_zone.run()
        if hd_zone_enabled:
            my_hd_zone.run()
        time.sleep(wait)


if __name__ == '__main__':
    main()
