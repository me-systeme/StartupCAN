"""
gsv86can.py

ctypes-based Python wrapper for the GSV86CAN Windows DLL.

This module provides:
- Constants and enums for common input types and adapter flags.
- A thon wrapper class (GSV86CAN) that exposes a Python-friendly API around
  the DLL functions used by this project.

Design goals:
- Keep the wrapper minimal and explicit: one Python method per DLL call.
- Convert ctypes outputs into native Python types.
- Raise RuntimeError with the DLL's last error text for "hard" failures where
  the application should know the reason.
- Return None/[] for "soft" read failures (e.g., read_multiple), so aquisition
  code can continue without killing the thread.

Important notes:
- Channel numbering in the DLL is typically 1-based (Chan 1..8). Some functions 
  accept Chan=0 to apply to all channels (see DLL header).
- This wrapper assumes the PCAN adapter type and GSV-6 flag as used in this
  project.
"""

import ctypes as ct 
from pathlib import Path

from startupcan.config import MYBUFFERSIZE, CANBAUD, DLL_PATH



# ------------------------------------------------------------------------------
# DLL return codes (as used in the provided header/examples)
# ------------------------------------------------------------------------------
GSV_ERROR = -1
GSV_TRUE = 1


# ------------------------------------------------------------------------------
# Adapter type / activation flags
# ------------------------------------------------------------------------------  
CANADAPTERTYPE_PCAN = 0x01
AE_FLAG_TYPEGSV6 = 0x0010


# ------------------------------------------------------------------------------
# Input type constants (from DLL header)
# ------------------------------------------------------------------------------
INTYP_BRIDGE_US875 = 0
INTYP_BRIDGE_US5   = 1
INTYP_BRIDGE_US25  = 2
INTYP_SE10         = 3
INTYP_PT1000       = 4
INTYP_TEMP_K       = 5

_IN_TYPE_NAMES = {
    INTYP_BRIDGE_US875: "Bridge (8.75V)",
    INTYP_BRIDGE_US5:   "Bridge (5V)",
    INTYP_BRIDGE_US25:  "Bridge (2.5V)",
    INTYP_SE10:         "SingleEnded ±10V",
    INTYP_PT1000:       "PT1000",
    INTYP_TEMP_K:       "Type K",
}

# ------------------------------------------------------------------------------
# CAN settings indices (from DLL header)
# ------------------------------------------------------------------------------
CANSET_CAN_IN_CMD_ID     = 0  # CAN_IN command Can-ID
CANSET_CAN_OUT_ANS_ID    = 1  # CAN_OUT response Can-ID
CANSET_CAN_CV_VALUE_ID   = 2  # CAN_CV value Can-ID
CANSET_CAN_CAST_MCAST_ID = 3  # CAN_CAST multicast Can-ID
CANSET_CAN_BAUD_HZ       = 4  # CAN_BAUD baudrate (in Hz)
CANSET_CAN_FLAGS         = 5  # CAN_FLAGS

CAN_ID_STD_MAX = 0x7FF
CAN_ID_EXT_FLAG = 0x80000000

class GSV86CAN:
    """
    Python wrapper for the GSV86CAN DLL.

    What happens in this class:
    - Loads the DLL via ctypes (WinDLL).
    - Defines argtypes/restype for every used DLL function.
    - Exposes Python methods that translate between Python types and ctypes.

    Parameters:
    ----------
    dll_path : str | Path
        Path to the GSV86CAN.dll file. Defaults to DLL_PATH from config.py
    
    Returns
    -------
    None
    """
    def __init__(self, dll_path=DLL_PATH):
        # ---------------------------------------------------------------------
        # Load DLL from the configured path
        # ---------------------------------------------------------------------
        dll_path = Path(dll_path)
        if not dll_path.exists():
            raise FileNotFoundError(f"'{dll_path}' was not found next to {__file__}")

        self.dll = ct.WinDLL(str(dll_path))

        # ---------------------------------------------------------------------
        # Define DLL function signatures (argtypes/restype)
        # ---------------------------------------------------------------------
        self.dll.GSV86CANDllVersion.argtypes = [ct.POINTER(ct.c_ulong)]
        self.dll.GSV86CANDllVersion.restype = ct.c_int

        self.dll.GSV86CANactivateExtended.argtypes = [
            ct.c_int,              # DevNo
            ct.c_int,              # adapter type
            ct.c_ulong,            # baud
            ct.c_int,              # buffer size
            ct.POINTER(ct.c_int),  # out chanNo
            ct.c_ulong,            # CANID cmd
            ct.c_ulong,            # CANID answer
            ct.c_ulong             # flags
        ]
        self.dll.GSV86CANactivateExtended.restype = ct.c_int

        self.dll.GSV86CANstartTX.argtypes = [ct.c_int]
        self.dll.GSV86CANstartTX.restype = ct.c_int

        self.dll.GSV86CANreadMultiple.argtypes = [
            ct.c_int,                 # DevNo
            ct.c_int,                 # (often 0 in examples)
            ct.POINTER(ct.c_double),  # out values
            ct.c_int,                 # max count
            ct.POINTER(ct.c_int),     # out valCnt
            ct.POINTER(ct.c_int),     # out errArr
        ]
        self.dll.GSV86CANreadMultiple.restype = ct.c_int

        self.dll.GSV86CANgetLastErrorText.argtypes = [
            ct.c_int,
            ct.c_char_p,
            ct.POINTER(ct.c_ulong)
        ]
        self.dll.GSV86CANgetLastErrorText.restype = ct.c_int

        self.dll.GSV86CANrelease.argtypes = [ct.c_int]
        self.dll.GSV86CANrelease.restype = ct.c_int

        self.dll.GSV86CANgetSerialNo.argtypes = [ct.c_int, ct.POINTER(ct.c_ulong)]
        self.dll.GSV86CANgetSerialNo.restype = ct.c_int

        self.dll.GSV86CANsetFrequency.argtypes = [ct.c_int, ct.c_double]
        self.dll.GSV86CANsetFrequency.restype = ct.c_int

        self.dll.GSV86CANgetInTypeRange.argtypes = [
            ct.c_int,                    # DevNo
            ct.c_int,                    # Chan (1..8)
            ct.POINTER(ct.c_int),        # out InType
            ct.POINTER(ct.c_double),     # out Range
        ]
        self.dll.GSV86CANgetInTypeRange.restype = ct.c_int

        self.dll.GSV86CANsetZero.argtypes = [ct.c_int, ct.c_int]  # DevNo, Chan
        self.dll.GSV86CANsetZero.restype = ct.c_int

        self.dll.GSV86CANwriteUserScale.argtypes = [
            ct.c_int,      # DevNo
            ct.c_int,      # Chan (1..8)
            ct.c_double,   # Norm
        ]
        self.dll.GSV86CANwriteUserScale.restype = ct.c_int

        self.dll.GSV86CANloadSettings.argtypes = [
            ct.c_int,  # DevNo
            ct.c_int,  # DataSetNo
        ]
        self.dll.GSV86CANloadSettings.restype = ct.c_int

        self.dll.GSV86CANMEwriteInputRange.argtypes = [
            ct.c_int,      # DevNo
            ct.c_int,      # Chan (0..6/8, 0 sets all channels)
            ct.c_int,      # type (GSV-6: 0)
            ct.c_double,   # Range (encoded by DLL expectation)
        ]
        self.dll.GSV86CANMEwriteInputRange.restype = ct.c_int

        self.dll.GSV86CANreadAoutScale.argtypes = [
            ct.c_int,                 # DevNo
            ct.c_int,                 # Chan (1..8)
            ct.POINTER(ct.c_double),  # out Scale
        ]
        self.dll.GSV86CANreadAoutScale.restype = ct.c_int

        self.dll.GSV86CANwriteAoutScale.argtypes = [
            ct.c_int,      # DevNo
            ct.c_int,      # Chan (1..8)
            ct.c_double,   # Scale
        ]
        self.dll.GSV86CANwriteAoutScale.restype = ct.c_int

        self.dll.GSV86CANgetCANSettings.argtypes = [
            ct.c_int,                 # DevNo
            ct.c_int,                 # Index
            ct.POINTER(ct.c_ulong),   # out Settings (unsigned long*)
        ]
        self.dll.GSV86CANgetCANSettings.restype = ct.c_int

        self.dll.GSV86CANsetCANSettings.argtypes = [
            ct.c_int,        # DevNo
            ct.c_int,        # Index
            ct.c_ulong,      # Settings (unsigned long)
        ]
        self.dll.GSV86CANsetCANSettings.restype = ct.c_int

    # -------------------------------------------------------------------------
    # Basic information / lifecycle
    # -------------------------------------------------------------------------
    def dll_version(self) -> int:
        """
        Read the DLL version.

        Returns
        -------
        int
            DLL version as returned by GSV86CANDllVersion().
        """
        v = ct.c_ulong()
        r = self.dll.GSV86CANDllVersion(ct.byref(v))
        if r == GSV_ERROR:
            raise RuntimeError("GSV86CANDllVersion failed")
        return int(v.value)
    
    def release(self, dev_no: int = 0):
        """
        Release devices in the DLL.

        Parameters
        ----------
        dev_no : int
            Device number to release. Many DLLs treat dev_no=0 as "release all".
            This project uses dev_no=0 as the default.

        Returns
        -------
        None
        """
        self.dll.GSV86CANrelease(dev_no)

    # -------------------------------------------------------------------------
    # Device setup / configuration
    # -------------------------------------------------------------------------
    def activate(self, dev_no: int, cmd_id: int, answer_id: int) -> int:
        """
        Activate a device on the CAN bus and configure its IDs.

        What happens:
        - Calls GSV86CANactivateExtended() using PCAN adapter, configured CAN baud,
          and the configured DLL buffer size.

        Parameters
        ----------
        dev_no : int
            Device index used by the DLL.
        cmd_id : int
            CAN command ID.
        answer_id : int
            CAN answer ID.

        Returns
        -------
        int
            Number of active channels as returned by the DLL.

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        chan_no = ct.c_int(0)
        r = self.dll.GSV86CANactivateExtended(
            dev_no,
            CANADAPTERTYPE_PCAN,
            CANBAUD,
            MYBUFFERSIZE,
            ct.byref(chan_no),
            cmd_id,
            answer_id,
            AE_FLAG_TYPEGSV6
        )
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
        return int(chan_no.value)

    def start_tx(self, dev_no: int):
        """
        Start device transmission.

        Parameters
        ----------
        dev_no : int
            Device number.

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANstartTX(dev_no)
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))

    def set_frequency(self, dev_no: int, frequency: float):
        """
        Configure the device sampling/transmission frequency.

        Parameters
        ----------
        dev_no : int
            Device number.
        frequency_hz : float
            Target frequency in Hz.

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANsetFrequency(dev_no, ct.c_double(frequency))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
    
    def load_settings(self, dev_no: int, dataset_no: int = 0):
        """
        Load a saved setup into the measurement amplifier.

        Parameters
        ----------
        dev_no : int
            Device number.
        dataset_no : int
            Dataset index (per DLL header):
            - 0 = last setup
            - 1 = factory default
            - 2..6 = user setup (GSV-8 only)

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANloadSettings(dev_no, int(dataset_no))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
    
    # -------------------------------------------------------------------------
    # Device status / metadata
    # -------------------------------------------------------------------------
    def get_can_settings(self, dev_no: int, index: int) -> int:
        """
        Read CAN settings value from the device.

        Mirrors the C function:
            int GSV86CANgetCANSettings(int DevNo, int Index, unsigned long *Settings)

        Parameters
        ----------
        dev_no : int
            Device number.
        index : int
            Settings index.

        Returns
        -------
        int
            Settings value (unsigned long) as Python int.

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        settings = ct.c_ulong(0)

        r = self.dll.GSV86CANgetCANSettings(dev_no, int(index), ct.byref(settings))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))

        return int(settings.value)
    
    def set_can_settings(self, dev_no: int, index: int, settings: int) -> None:
        """
        Write CAN settings value to the device.

        Mirrors the C function:
            int GSV86CANsetCANSettings(int DevNo, int Index, unsigned long Settings)

        Index meanings (per header):
            0: CAN_IN  command Can-ID
            1: CAN_OUT response Can-ID
            2: CAN_CV  value Can-ID
            3: CAN_CAST multicast Can-ID
            4: CAN_BAUD baudrate (Hz)
            5: CAN_FLAGS

        Settings meanings (per header):
            can-ID:
              0x00000000-0x000007FF: std-id
              0x80000000-0x9FFFFFFF: ext-id (0x80000000 flag set)
            baudrate:
              1000000, 500000, 250000, 125000, 100000, 50000, 25000, 12500, 10000

        Raises
        ------
        RuntimeError if the DLL returns GSV_ERROR.
        """
        # Ensure 32-bit unsigned range (what the DLL expects)
        val = ct.c_ulong(int(settings) & 0xFFFFFFFF)

        r = self.dll.GSV86CANsetCANSettings(dev_no, int(index), val)
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))

    def get_serial_no(self, dev_no: int) -> int:
        """
        Read the device serial number.

        Parameters
        ----------
        dev_no : int
            Device number.

        Returns
        -------
        int
            Serial number.

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        ser = ct.c_ulong(0)
        r = self.dll.GSV86CANgetSerialNo(dev_no, ct.byref(ser))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
        return int(ser.value)
    
    def last_error_text(self, dev_no: int) -> str:
        """
        Read the last error message from the DLL.

        Parameters
        ----------
        dev_no : int
            Device number (some DLLs store error state per device).

        Returns
        -------
        str
            Error message string with numeric error code.
        """
        buf = ct.create_string_buffer(256)
        code = ct.c_ulong(0)
        self.dll.GSV86CANgetLastErrorText(dev_no, buf, ct.byref(code))
        return f"{buf.value.decode(errors='ignore')} (Code={code.value})"
    
    # -------------------------------------------------------------------------
    # Acquisition
    # -------------------------------------------------------------------------
    def read_multiple(self, dev_no: int, max_items: int):
        """
        Read buffered samples from the DLL.

        What happens:
        - Allocates ctypes buffers for values and error array.
        - Calls GSV86CANreadMultiple().
        - Returns:
          - None on DLL error (to allow acquisition loops to continue),
          - [] if no values are available,
          - list[float] of values if data was returned.

        Parameters
        ----------
        dev_no : int
            Device number.
        max_items : int
            Maximum number of doubles to read.

        Returns
        -------
        list[float] | [] | None
            - list[float]: values returned by the DLL
            - []: no values available
            - None: DLL returned GSV_ERROR
        """
        values = (ct.c_double * max_items)()
        errarr = (ct.c_int * max_items)()
        valcnt = ct.c_int(0)

        r = self.dll.GSV86CANreadMultiple(dev_no, 0, values, max_items, ct.byref(valcnt), errarr)

        if r == GSV_ERROR:
            return None
        
        if r == GSV_TRUE and valcnt.value > 0:
            return [values[i] for i in range(valcnt.value)]
        
        return []
    
    # -------------------------------------------------------------------------
    # Channel / scaling / input configuration
    # -------------------------------------------------------------------------
    def set_zero(self, dev_no: int, chan: int = 0):
        """
        Zero (tare) the device or a specific channel.

        Parameters
        ----------
        dev_no : int
            Device number.
        chan : int
            Channel index according to DLL header. Many devices use:
            - chan=0: apply to all channels
            - chan=1..8: apply to the selected channel

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANsetZero(dev_no, chan)
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))

    def write_user_scale(self, dev_no: int, chan_1_based: int, norm: float):
        """
        Program the user scaling factor into the device/DLL.

        Parameters
        ----------
        dev_no : int
            Device number.
        chan_1_based : int
            Channel index (1..8).
        norm : float
            Scaling factor (e.g., kN per "Norm unit") depending on the device/DLL.

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANwriteUserScale(dev_no, chan_1_based, ct.c_double(float(norm)))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
    
    def get_in_type_range(self, dev_no: int, chan_1_based: int):
        """
        Query the currently configured input type and input range.

        Parameters
        ----------
        dev_no : int
            Device number.
        chan_1_based : int
            Channel index (1..8). This DLL call is typically 1-based.

        Returns
        -------
        tuple[int, float]
            (in_type, range_value)

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        in_type = ct.c_int(0)
        rng = ct.c_double(0.0)

        r = self.dll.GSV86CANgetInTypeRange(dev_no, chan_1_based, ct.byref(in_type), ct.byref(rng))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))

        return int(in_type.value), float(rng.value)
    
    def write_input_range(self, dev_no: int, chan: int, in_type: int, mv_per_v: int):
        """
        Manually set the input sensitivity (input range).

        Parameters
        ----------
        dev_no : int
            Device number.
        chan : int
            Channel index used by the DLL:
            - 0: apply to all channels (per header comment)
            - otherwise: a specific channel index (DLL-specific range)
        in_type : int
            Type parameter expected by the DLL for this function.
            For GSV-6 this is typically 0.
        mv_per_v : float
            Range value to write (already encoded or raw, depending on how you
            want to treat it).

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        encoded = float(mv_per_v)

        r = self.dll.GSV86CANMEwriteInputRange(
            dev_no,
            int(chan),
            int(in_type),
            ct.c_double(encoded),
        )
        if r == GSV_ERROR:
            print(self.last_error_text(dev_no))
            raise RuntimeError(self.last_error_text(dev_no))
    
    def read_aout_scale(self, dev_no: int, chan_1_based: int) -> float:
        """
        Read the current analog output scale for a channel.

        Parameters
        ----------
        dev_no : int
            Device number.
        chan_1_based : int
            Channel index (1..8).

        Returns
        -------
        float
            Current analog output scale.

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        scale = ct.c_double(0.0)
        r = self.dll.GSV86CANreadAoutScale(dev_no, chan_1_based, ct.byref(scale))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))
        return float(scale.value)
    
    def write_aout_scale(self, dev_no: int, chan_1_based: int, scale: float):
        """
        Set the analog output scale for a channel.

        Parameters
        ----------
        dev_no : int
            Device number.
        chan_1_based : int
            Channel index (1..8).
        scale : float
            New scale value to program.

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If the DLL returns GSV_ERROR.
        """
        r = self.dll.GSV86CANwriteAoutScale(dev_no, chan_1_based, ct.c_double(float(scale)))
        if r == GSV_ERROR:
            raise RuntimeError(self.last_error_text(dev_no))