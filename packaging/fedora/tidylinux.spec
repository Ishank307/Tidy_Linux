Name:           tidylinux
Version:        1.4.0
Release:        1%{?dist}
Summary:        Native manager for portable Linux applications
License:        GPL-3.0-or-later
URL:            https://github.com/Ishank307/Tidy_Linux
# Source0 is generated locally until a release tag is published.
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  python3-pytest
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
Requires:       python3-gobject
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Recommends:     squashfs-tools

%description
TidyLinux installs portable archives and AppImages into user-owned storage,
with transactional updates, rollback, manual release checks, and explicit
cleanup review. It does not manage RPM or Flatpak installations.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files tidylinux
install -Dpm 0644 data/io.github.tidylinux.TidyLinux.desktop %{buildroot}%{_datadir}/applications/io.github.tidylinux.TidyLinux.desktop
install -Dpm 0644 data/icons/io.github.tidylinux.TidyLinux.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.tidylinux.TidyLinux.svg
install -Dpm 0644 data/io.github.tidylinux.TidyLinux.metainfo.xml %{buildroot}%{_metainfodir}/io.github.tidylinux.TidyLinux.metainfo.xml

%check
PYTHONPATH=src %{python3} -m pytest -q
PYTHONPATH=src %{python3} -m unittest discover -s tests -q
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.tidylinux.TidyLinux.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/io.github.tidylinux.TidyLinux.metainfo.xml

%files -f %{pyproject_files}
%license LICENSE
%doc README.md docs CONTRIBUTING.md
%{_bindir}/tidylinux
%{_datadir}/applications/io.github.tidylinux.TidyLinux.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.tidylinux.TidyLinux.svg
%{_metainfodir}/io.github.tidylinux.TidyLinux.metainfo.xml

%changelog
* Wed Nov 18 2026 TidyLinux contributors - 1.4.0-1
- Prepare Fedora desktop packaging; no user-data lifecycle scriptlets.
