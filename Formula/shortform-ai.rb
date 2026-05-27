class ShortformAi < Formula
  include Language::Python::Virtualenv

  desc "Local-first CLI for short-form video and creator analysis"
  homepage "https://github.com/intelc/shortform-ai"
  url "https://github.com/intelc/shortform-ai/archive/refs/tags/v0.1.12.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_TARBALL_SHA256"
  license "MIT"

  depends_on "python@3.12"
  depends_on "ffmpeg"

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/source/c/cryptography/cryptography-45.0.7.tar.gz"
    sha256 "REPLACE_WITH_CRYPTOGRAPHY_SHA256"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/source/c/certifi/certifi-2025.8.3.tar.gz"
    sha256 "REPLACE_WITH_CERTIFI_SHA256"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/source/c/charset_normalizer/charset_normalizer-3.4.3.tar.gz"
    sha256 "REPLACE_WITH_CHARSET_NORMALIZER_SHA256"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/source/i/idna/idna-3.10.tar.gz"
    sha256 "REPLACE_WITH_IDNA_SHA256"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/source/r/requests/requests-2.32.5.tar.gz"
    sha256 "REPLACE_WITH_REQUESTS_SHA256"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/source/u/urllib3/urllib3-2.5.0.tar.gz"
    sha256 "REPLACE_WITH_URLLIB3_SHA256"
  end

  resource "yt-dlp" do
    url "https://files.pythonhosted.org/packages/source/y/yt-dlp/yt_dlp-2025.8.22.tar.gz"
    sha256 "REPLACE_WITH_YT_DLP_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "shortform-ai 0.1.12", shell_output("#{bin}/shortform-ai --version")
    output = shell_output("#{bin}/shortform-ai doctor")
    assert_match "content_ai_version", output
    assert_match "shortform_ai_version", output
    assert_match "python_cryptography", output
  end
end
