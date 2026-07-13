{ pkgs, lib, ... }:
{
  name = "multi-arch-test";
  meta.maintainers = with pkgs.lib.maintainers; [ tfc ];

  node.pkgsReadOnly = false;

  nodes = {
    # Setting build = host platform because such standard VM setups
    # are well-cached by the multi-arch build infrastructure of the
    # project.
    vmIntel = {
      nixpkgs.hostPlatform = "x86_64-linux";
      virtualisation.qemu.package = lib.mkForce pkgs.qemu_test;
    };
    vmArm = {
      nixpkgs.hostPlatform = "aarch64-linux";
      virtualisation.qemu.package = lib.mkForce pkgs.qemu_test;
    };
  };

  defaults = {
    # not needed for this test and shrinks the closure
    # because we dont' need qemu for all architectures
    virtualisation.qemu.guestAgent.enable = false;

    # the test is already very slow but we don't need DHCP anyway
    networking.dhcpcd.enable = false;

    # fix the network-online target, otherwise it doesn't work
    # properly as a synchronization mechanism.
    systemd.targets.network-online = {
      wants = [ "network-addresses-eth1.service" ];
      after = [ "network-addresses-eth1.service" ];
    };
  };

  # slow due to SW emulation but shouldn't be slower than that
  globalTimeout = 5 * 60;

  testScript = /* python */ ''
    start_all()

    vmIntel.systemctl("start network-online.target")
    vmArm.systemctl("start network-online.target")
    vmIntel.wait_for_unit("network-online.target")
    vmArm.wait_for_unit("network-online.target")

    vmIntel.succeed("ping -c 1 vmArm")
    vmArm.succeed("ping -c 1 vmIntel")

    archIntel = vmIntel.succeed("uname -m")
    t.assertIn("x86_64", archIntel, "machine1 is an intel VM")
    archArm = vmArm.succeed("uname -m")
    t.assertIn("aarch64", archArm, "machine1 is an ARM VM")
  '';
}
