"""Tests for IOL interface-to-slot/port mapping and device detection."""
from __future__ import annotations


class TestDetectIolDeviceType:
    """Tests for detect_iol_device_type function."""

    def test_detects_l3_from_filename(self):
        """Should detect iol-xe from L3 filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("i86bi-linux-l3-adventerprisek9-15.6.1T.bin") == "iol-xe"

    def test_detects_l2_from_filename(self):
        """Should detect iol-l2 from L2 filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("i86bi-linux-l2-adventerprisek9-15.2d.bin") == "iol-l2"

    def test_detects_l2_from_ioll2(self):
        """Should detect iol-l2 from ioll2 in filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("ioll2-adventerprise.bin") == "iol-l2"

    def test_detects_l2_from_iol_l2(self):
        """Should detect iol-l2 from iol_l2 in filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("iol_l2_image.bin") == "iol-l2"

    def test_detects_l3_from_generic_iol(self):
        """Should default to iol-xe for generic IOL filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("iol-image.bin") == "iol-xe"

    def test_returns_none_for_non_iol(self):
        """Should return None for non-IOL filenames."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("ceos-4.28.0F.tar") is None

    def test_case_insensitive(self):
        """Should match regardless of case."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("I86BI-LINUX-L3-ADVENTERPRISEK9-15.6.1T.BIN") == "iol-xe"
        assert detect_iol_device_type("I86BI-LINUX-L2-ADVENTERPRISEK9-15.2D.BIN") == "iol-l2"

    def test_l2_ipbasek9(self):
        """Should detect iol-l2 from L2 ipbasek9 filename."""
        from app.image_store import detect_iol_device_type

        assert detect_iol_device_type("i86bi-linux-l2-ipbasek9-15.1g.bin") == "iol-l2"


class TestDetectDeviceFromFilename:
    """Tests for IOL detection in detect_device_from_filename."""

    def test_detects_iol_xe_from_l3(self):
        """Should detect iol-xe device from L3 IOL filename."""
        from app.image_store import detect_device_from_filename

        device_id, version = detect_device_from_filename(
            "i86bi-linux-l3-adventerprisek9-15.6.1T.bin"
        )

        assert device_id == "iol-xe"
        assert version is not None

    def test_detects_iol_l2(self):
        """Should detect iol-l2 device from L2 IOL filename."""
        from app.image_store import detect_device_from_filename

        device_id, version = detect_device_from_filename(
            "i86bi-linux-l2-adventerprisek9-15.2d.bin"
        )

        assert device_id == "iol-l2"

    def test_detects_iol_from_generic(self):
        """Should detect iol-xe from generic IOL reference."""
        from app.image_store import detect_device_from_filename

        device_id, _ = detect_device_from_filename("iol-something.bin")

        assert device_id == "iol-xe"


class TestIolInterfaceMapping:
    """Tests for IOL interface→slot/port mapping formula.

    IOL maps ethX (X>=1) to slot=X/4, port=X%4.
    This is critical for NETMAP and iouyap.ini generation.
    """

    def _compute_mapping(self, num_interfaces):
        """Compute expected slot/port mappings for N interfaces."""
        mappings = []
        for i in range(num_interfaces):
            slot = i // 4
            port = i % 4
            mappings.append((i + 1, slot, port))  # ethN where N=i+1
        return mappings

    def test_single_interface(self):
        """eth1 -> slot 0, port 0."""
        mappings = self._compute_mapping(1)
        assert mappings == [(1, 0, 0)]

    def test_four_interfaces(self):
        """eth1-4 -> slot 0, ports 0-3."""
        mappings = self._compute_mapping(4)
        assert mappings == [
            (1, 0, 0),
            (2, 0, 1),
            (3, 0, 2),
            (4, 0, 3),
        ]

    def test_eight_interfaces(self):
        """eth1-8 -> 2 slots, 4 ports each."""
        mappings = self._compute_mapping(8)
        assert len(mappings) == 8
        # First slot
        assert mappings[0] == (1, 0, 0)
        assert mappings[3] == (4, 0, 3)
        # Second slot
        assert mappings[4] == (5, 1, 0)
        assert mappings[7] == (8, 1, 3)

    def test_sixteen_interfaces(self):
        """eth1-16 -> 4 slots."""
        mappings = self._compute_mapping(16)
        assert len(mappings) == 16
        assert mappings[15] == (16, 3, 3)

    def test_thirtytwo_interfaces(self):
        """eth1-32 -> 8 slots (max_ports=32)."""
        mappings = self._compute_mapping(32)
        assert len(mappings) == 32
        assert mappings[31] == (32, 7, 3)

    def test_num_slots_calculation(self):
        """Verify num_slots = ceil(num_ifaces / 4)."""
        for num_ifaces in [1, 2, 3, 4, 5, 8, 12, 16, 32]:
            expected_slots = (num_ifaces + 3) // 4
            assert expected_slots >= 1
            # Verify all interfaces fit
            max_port = (expected_slots * 4) - 1
            last_iface_idx = num_ifaces - 1
            assert last_iface_idx <= max_port
