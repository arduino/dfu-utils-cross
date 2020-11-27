#!/bin/bash -xe
# Copyright (c) 2016 Arduino LLC
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

mkdir -p distrib/linux64
cd libusb
export LIBUSB_DIR=`pwd`
CFLAGS="-lrt" ./configure --enable-static --disable-shared --disable-udev
make clean
make
cd ..
cd dfu-util
CFLAGS="-lrt" USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util  ../distrib/linux64/
cd ..

mkdir -p distrib/linux32
cd libusb
export LIBUSB_DIR=`pwd`
CFLAGS="-m32 -lrt" ./configure  --enable-static --disable-shared --disable-udev
make clean
make
cd ..
cd dfu-util
CFLAGS="-m32 -lrt" USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/linux32
cd ..

mkdir -p distrib/arm
cd libusb
export LIBUSB_DIR=`pwd`
./configure --enable-static --disable-shared --disable-udev --host=arm-linux-gnueabihf
make clean
make
cd ..
cd dfu-util
USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure --host=arm-linux-gnueabihf
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/arm/
