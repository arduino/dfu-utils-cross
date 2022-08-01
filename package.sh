#!/bin/bash -ex
# Copyright (c) 2014-2016 Arduino LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

OUTPUT_VERSION=0.11.0-arduino3

export OS=`uname -o || uname`

#add osxcross, mingw and arm-linux-gnueabihf paths to PATH

cd libusb && ./bootstrap.sh && cd ..
cd dfu-util && ./autogen.sh && patch -p1 < ../silence_warnings.patch && cd ..

./compile_win.sh
./compile_linux.sh
./compile_mac.sh

package_index=`cat package_index.template | sed s/%%VERSION%%/${OUTPUT_VERSION}/`

cd distrib

rm -f *.bz2

folders=`ls`
t_os_arr=($folders)

for t_os in "${t_os_arr[@]}"
do
	FILENAME=dfu-util-${OUTPUT_VERSION}-${t_os}.tar.bz2
	tar -cjvf ${FILENAME} ${t_os}/
	SIZE=`stat --printf="%s" ${FILENAME}`
	SHASUM=`sha256sum ${FILENAME} | cut -f1 -d" "`
	T_OS=`echo ${t_os} | awk '{print toupper($0)}'`
	echo $T_OS
	package_index=`echo $package_index | 
		sed s/%%FILENAME_${T_OS}%%/${FILENAME}/ |
		sed s/%%FILENAME_${T_OS}%%/${FILENAME}/ |
		sed s/%%SIZE_${T_OS}%%/${SIZE}/ |
		sed s/%%SHA_${T_OS}%%/${SHASUM}/`
done
cd -

set +x

echo ================== CUT ME HERE =====================

echo ${package_index} | python -m json.tool
