{ buildPythonPackage
, cffi
, fetchFromGitHub
, lib
, libpulseaudio
, numpy
, setuptools
, testers
}:

let
  # TODO: fix version and use according git/pypy tag
  version = "1.2.3";
in

buildPythonPackage (finalAttrs: {
  pname = "soundcard";
  inherit version;
  pyproject = true;

  # TODO: use fetchpypy
  src = fetchFromGitHub {
    # TODO: use
    # inherit (finalAttrs) version;
    owner = "bastibe";
    repo = "SoundCard";
    rev = "6dec3072ff9087c2f6b24f0f56359190eb2b8611";
    hash = "sha256-d91fhgkjnRFamMIn7hmXuHZt6kGBysAhQq8IsZ/WqHs=";
  };

  patchPhase = ''
    substituteInPlace soundcard/pulseaudio.py \
      --replace "'pulse'" "'${libpulseaudio}/lib/libpulse.so'"
  '';

  nativeBuildInputs = [ setuptools ];

  propagatedBuildInputs = [
    cffi
    numpy
  ];

  # doesn't work because there are not many soundcards in the
  # sandbox. See VM-test
  #pythonImportsCheck = [ "soundcard" ];

  doCheck = false;

  passthru.tests.vm-with-soundcard = testers.runNixOSTest ./test.nix;

  meta = with lib; {
    description = "A Pure-Python Real-Time Audio Library";
    homepage = "https://soundcard.readthedocs.io";
    changelog = "https://soundcard.readthedocs.io/en/latest/#changelog";
    license = licenses.bsd3;
    # TODO: Put yourself here as maintainer
    maintainers = with maintainers; [ tfc ];
  };
})
