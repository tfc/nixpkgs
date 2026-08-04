{
  stdenv,
  lib,
  fetchFromGitHub,
  fetchpatch,
  cmake,
  pkg-config,
  alsaSupport ? stdenv.hostPlatform.isLinux,
  alsa-lib,
  jackSupport ? true,
  libjack2,
  coremidiSupport ? stdenv.hostPlatform.isDarwin,
}:

stdenv.mkDerivation rec {
  pname = "rtmidi";
  version = "6.0.0";

  src = fetchFromGitHub {
    owner = "thestk";
    repo = "rtmidi";
    tag = version;
    hash = "sha256-QuUeFx8rPpe0+exB3chT6dUceDa/7ygVy+cQYykq7e0=";
  };

  nativeBuildInputs = [
    cmake
    pkg-config
  ];

  buildInputs = lib.optional alsaSupport alsa-lib ++ lib.optional jackSupport libjack2;

  cmakeFlags = [
    (lib.cmakeBool "RTMIDI_API_ALSA" alsaSupport)
    (lib.cmakeBool "RTMIDI_API_JACK" jackSupport)
    (lib.cmakeBool "RTMIDI_API_CORE" coremidiSupport)
  ];

  meta = {
    description = "Set of C++ classes that provide a cross platform API for realtime MIDI input/output";
    homepage = "https://www.music.mcgill.ca/~gary/rtmidi/";
    license = lib.licenses.mit;
    maintainers = with lib.maintainers; [ magnetophon ];
    platforms = lib.platforms.unix;
  };
}
