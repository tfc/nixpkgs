{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.paretosecurity;
in
{

  options.services.paretosecurity = {
    enable = lib.mkEnableOption "[ParetoSecurity](https://paretosecurity.com) [agent](https://github.com/ParetoSecurity/agent) and its root helper";
    package = lib.mkPackageOption pkgs "paretosecurity" { };
    trayIcon = lib.mkEnableOption "tray icon for ParetoSecurity";
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];
    systemd.packages = [ cfg.package ];

    # In traditional Linux distributions, systemd would read the [Install] section from
    # unit files and automatically create the appropriate symlinks to enable services.
    # However, in NixOS, due to its immutable nature and the way the Nix store works,
    # the [Install] sections are not processed during system activation. Instead, we
    # must explicitly tell NixOS which units to enable by specifying their target
    # dependencies here. This creates the necessary symlinks in the proper locations.
    systemd.sockets.paretosecurity.wantedBy = [ "sockets.target" ];

    systemd.user = lib.mkIf cfg.trayIcon {
      services = {
        paretosecurity-trayicon.wantedBy = [ "graphical-session.target" ];
        paretosecurity-user.wantedBy = [ "graphical-session.target" ];
      };
      timers.paretosecurity-user.wantedBy = [ "timers.target" ];
    };
  };
}
