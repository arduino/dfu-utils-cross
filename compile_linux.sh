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

mkdir -p distrib/linux64
cd libusb
export LIBUSB_DIR=`pwd`
CFLAGS="-lrt" ./configure --enable-static --disable-shared --disable-udev --host=x86_64-ubuntu16.04-linux-gnu
make clean
make
cd ..
cd dfu-util
CFLAGS="-lrt" USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure --host=x86_64-ubuntu16.04-linux-gnu
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util  ../distrib/linux64/
cd ..

mkdir -p distrib/linux32
cd libusb
export LIBUSB_DIR=`pwd`
CFLAGS="-lrt" ./configure  --enable-static --disable-shared --disable-udev --host=i686-ubuntu16.04-linux-gnu
make clean
make
cd ..
cd dfu-util
CFLAGS="-lrt" USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure --host=i686-ubuntu16.04-linux-gnu
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
cd ..

mkdir -p distrib/arm64
cd libusb
export LIBUSB_DIR=`pwd`
./configure --enable-static --disable-shared --disable-udev --host=aarch64-linux-gnu
make clean
make
cd ..
cd dfu-util
USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure --host=aarch64-linux-gnu
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/arm64/
cd ..