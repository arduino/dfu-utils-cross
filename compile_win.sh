#!/bin/bash -xe

export CFLAGS="-mno-ms-bitfields"
mkdir -p distrib/windows
cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
./configure --host=i686-w64-mingw32 --enable-static --disable-shared
make clean
make -j4
cd ..
cd dfu-util-0.8
USB_CFLAGS=-I$LIBUSB_DIR/libusb/ USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0" ./configure --host=i686-w64-mingw32
make clean
CFLAGS=-static make
cp src/*.exe ../distrib/windows/
