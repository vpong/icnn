N=10
total=100000
trainTimestep=1000
testEpisode=1
task="Hopper-v1"
outdir="output/Hopper-v1/"
monitor=-1

#DDPG
for ((i=0;i<$N;i++))
do
  python src/main.py --model DDPG --env $task --outdir $outdir/DDPG/$i \
    --total $total --train $trainTimestep --test $testEpisode \
    --tfseed $i --gymseed $i --npseed $i --monitor $monitor --l2norm 0.001
done

#NAF
for ((i=0;i<$N;i++))
do
  python src/main.py --model NAF --env $task --outdir $outdir/NAF/$i \
    --total $total --train $trainTimestep --test $testEpisode --reward_k 0.3\
    --tfseed $i --gymseed $i --npseed $i  --monitor $monitor  --l2norm 0.001
done

#ICNN
for ((i=0;i<$N;i++))
do
  python src/main.py --model ICNN --env $task --outdir $outdir/ICNN/$i \
    --total $total --train $trainTimestep --test $testEpisode --reward_k 0.1\
    --tfseed $i --gymseed $i --npseed $i --monitor $monitor --l2norm 0.001 --icnn_bn True
done

#plot
python src/plot.py --runs $N --total $total --train $trainTimestep --data $outdir --min -500 --max 2500
