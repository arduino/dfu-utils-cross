#!/bin/bash -xe

mkdir -p distrib/arm

#disable pkg-config
export PKG_CONFIG_PATH=`pwd`

cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
./configure --enable-static --disable-shared --host=arm-linux-gnueabihf
make clean
make -j4
cd ..

#cd kmod-22
#export KMOD_DIR=`pwd`
#./configure --host=arm-linux-gnueabihf
#make clean
#make -j4
#cd ..

#export KMOD_CFLAGS="-I$KMOD_DIR/libkmod/"
#export KMOD_LIBS="-L$KMOD_DIR/libkmod/.libs/ -lkmod"

cd eudev-3.1.5
export UDEV_DIR=`pwd`
./configure --enable-static --disable-shared --host=arm-linux-gnueabihf --disable-blkid --disable-kmod
make clean
make -j4
cd ..

export libusb_CFLAGS="-I$LIBUSB_DIR/libusb/"
export libusb_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread"
export libudev_CFLAGS="-I$UDEV_DIR/src/libudev/"
export libudev_LIBS="-L$UDEV_DIR/src/libudev/.libs/ -ludev"

cd hidapi
./bootstrap
export HIDAPI_DIR=`pwd`
./configure --enable-static --disable-shared --host=arm-linux-gnueabihf
make clean
make -j4
cd ..

cd OpenOCD
./bootstrap
export LIBUSB1_CFLAGS="-I$LIBUSB_DIR/libusb/" 
export LIBUSB1_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" 
export HIDAPI_CFLAGS="-I$HIDAPI_DIR/hidapi/"
export HIDAPI_LIBS="-L$HIDAPI_DIR/linux/.libs/ -L$HIDAPI_DIR/libusb/.libs/ -lhidapi-hidraw -lhidapi-libusb" 
export CFLAGS="-DHAVE_LIBUSB_ERROR_NAME"
PKG_CONFIG_PATH=`pwd` ./configure --host=arm-linux-gnueabihf
make clean
CFLAGS=-static make
#cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/arm/
