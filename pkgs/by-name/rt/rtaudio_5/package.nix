{
  stdenv,
  lib,
  config,
  fetchFromGitHub,
  cmake,
  pkg-config,
  alsaSupport ? stdenv.hostPlatform.isLinux,
  alsa-lib,
  pulseaudioSupport ? config.pulseaudio or stdenv.hostPlatform.isLinux,
  libpulseaudio,
  jackSupport ? true,
  libjack2,
  coreaudioSupport ? stdenv.hostPlatform.isDarwin,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "rtaudio";
  version = "5.2.0";

  # nixpkgs-update: no auto update
  src = fetchFromGitHub {
    owner = "thestk";
    repo = "rtaudio";
    rev = finalAttrs.version;
    sha256 = "0xvahlfj3ysgsjsp53q81hayzw7f99n1g214gh7dwdr52kv2l987";
  };

  nativeBuildInputs = [
    cmake
    pkg-config
  ];

  buildInputs =
    lib.optional alsaSupport alsa-lib
    ++ lib.optional pulseaudioSupport libpulseaudio
    ++ lib.optional jackSupport libjack2;

  cmakeFlags = [
    (lib.cmakeBool "RTAUDIO_API_ALSA" alsaSupport)
    (lib.cmakeBool "RTAUDIO_API_PULSE" pulseaudioSupport)
    (lib.cmakeBool "RTAUDIO_API_JACK" jackSupport)
    (lib.cmakeBool "RTAUDIO_API_CORE" coreaudioSupport)
  ];

  meta = {
    description = "Set of C++ classes that provide a cross platform API for realtime audio input/output";
    homepage = "https://www.music.mcgill.ca/~gary/rtaudio/";
    license = lib.licenses.mit;
    maintainers = with lib.maintainers; [ magnetophon ];
    platforms = lib.platforms.unix;
  };
})
