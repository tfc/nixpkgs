{
  lib,
  stdenv,
  runtimeShell,
  fetchurl,
  unzip,
  mono,
  avrdude,
  gtk2,
  xdg-utils,
}:

stdenv.mkDerivation {
  pname = "avrdudess";
  version = "2.18";

  src = fetchurl {
    url = "https://github.com/ZakKemble/AVRDUDESS/releases/download/v2.18/AVRDUDESS-2.18-portable.zip";
    sha256 = "sha256-N93FLiXp1WwhI5KwH6sho2wyFtkbODwCHOpEVbVnYdc=";
  };

  nativeBuildInputs = [ unzip ];

  dontUnpack = true;
  dontInstall = true;

  buildPhase = ''
    mkdir -p "$out/avrdudess"
    mkdir -p "$out/bin"

    unzip "$src" -d "$out/avrdudess"

    cat >> "$out/bin/avrdudess" << __EOF__
    #!${runtimeShell}
    export LD_LIBRARY_PATH="${
      lib.makeLibraryPath [
        gtk2
        mono
      ]
    }"
    # We need PATH from user env for xdg-open to find its tools, which
    # typically depend on the currently running desktop environment.
    export PATH="\$PATH:${
      lib.makeBinPath [
        avrdude
        xdg-utils
      ]
    }"

    # avrdudess must have its resource files in its current working directory
    cd $out/avrdudess && exec ${mono}/bin/mono "$out/avrdudess/avrdudess.exe" "\$@"
    __EOF__

    chmod a+x "$out/bin/"*
  '';

  meta = with lib; {
    description = "GUI for AVRDUDE (AVR microcontroller programmer)";
    homepage = "https://blog.zakkemble.net/avrdudess-a-gui-for-avrdude/";
    changelog = "https://github.com/ZakKemble/AVRDUDESS/blob/v${version}/Changelog.txt";
    license = licenses.gpl3;
    platforms = platforms.linux;
    maintainers = [ maintainers.bjornfor ];
    mainProgram = "avrdudess";
  };
}
