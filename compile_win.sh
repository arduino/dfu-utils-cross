#!/bin/bash -xe
# Copyright (c) 2016 Arduino LLC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

export CFLAGS="-mno-ms-bitfields"
mkdir -p distrib/windows
cd libusb
export LIBUSB_DIR=`pwd`
CFLAGS="-mno-ms-bitfields -static" ./configure --host=i686-w64-mingw32 --enable-static --disable-shared
make clean
CFLAGS="-mno-ms-bitfields -static" make
cd ..
cd dfu-util
CFLAGS="-mno-ms-bitfields -static" USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0" ./configure --host=i686-w64-mingw32
make clean
CFLAGS="-mno-ms-bitfields -static" make
cp src/*.exe ../distrib/windows/
